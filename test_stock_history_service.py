from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from otc_index import TW_TZ
from stock_history_service import clear_stock_history_cache, get_stock_history_bars_1m, get_stock_history_bars_5m
from stock_history_service import _code_lock
from stock_bar_bootstrap import _history_slots


def ts(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=TW_TZ).timestamp() * 1000)


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def kbars(self, *, contract, start: str, end: str):
        self.calls.append((start, end))
        closes = []
        opens = []
        highs = []
        lows = []
        close_values = []
        volumes = []

        # 前一交易日最後一根 5m：13:25~13:30，Shioaji 1m ts 使用收棒時間。
        for minute, price in zip(range(26, 31), [80, 81, 82, 83, 84]):
            closes.append(datetime(2026, 8, 6, 13, minute, tzinfo=TW_TZ))
            opens.append(price)
            highs.append(price + 1)
            lows.append(price - 1)
            close_values.append(price + 0.5)
            volumes.append(10)

        # 今日 09:00~09:06 六根 1m。
        for minute, price in zip(range(1, 7), [100, 101, 102, 103, 104, 105]):
            closes.append(datetime(2026, 8, 7, 9, minute, tzinfo=TW_TZ))
            opens.append(price)
            highs.append(price + 1)
            lows.append(price - 1)
            close_values.append(price + 0.5)
            volumes.append(20)

        return {
            "ts": closes,
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": close_values,
            "Volume": volumes,
        }


class FakeService:
    def __init__(self) -> None:
        self.api = FakeApi()
        self.state = SimpleNamespace(logged_in=True)
        self.contract = object()
        self.subscriptions: list[list[str]] = []

    def ensure_stock_subscriptions(self, codes):
        rows = list(codes)
        self.subscriptions.append(rows)
        return {"requested": rows, "failed": {}}

    def _resolve_stock_contract(self, code: str):
        return self.contract if code == "2344" else None


class FakeHub:
    def get_live_bars(self, code: str):
        if code != "2344":
            return []
        return [
            # 與歷史 09:05 bucket 重複，必須由 live 覆蓋。
            {
                "ts": ts(2026, 8, 7, 9, 5),
                "open": 105,
                "high": 112,
                "low": 104,
                "close": 111,
                "volume": 99,
                "tick_count": 9,
            }
        ]

    def get_live_bars_1m(self, code: str):
        if code != "2344":
            return []
        return [
            # 與歷史 09:05 這根 1m 重複，必須由 live 覆蓋。
            {
                "ts": ts(2026, 8, 7, 9, 5),
                "open": 104,
                "high": 106,
                "low": 103,
                "close": 105.5,
                "volume": 77,
                "tick_count": 7,
            }
        ]


class StockHistoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_stock_history_cache()
        self.service = FakeService()
        self.hub = FakeHub()
        self.now_ms = ts(2026, 8, 7, 9, 7)

    def test_today_bars_come_only_from_live_hub_not_stale_kbars_snapshot(self):
        result = get_stock_history_bars_5m(
            "2344", calendar_days=14, service=self.service, hub=self.hub, now_ms=self.now_ms,
        )
        today_bars = [
            bar for bar in result["bars"]
            if datetime.fromtimestamp(bar["ts"] / 1000, TW_TZ).strftime("%Y-%m-%d") == "2026-08-07"
        ]
        # FakeApi的kbars另外還回傳09:01/09:02/09:03/09:04/09:06這幾根「今天」
        # 的資料，但這些不該進最終結果：kbars查到的「今天」常常是查詢當下還
        # 沒到齊的殘缺快照，一旦被快取住就會卡一整天，之後Hub即使收到更多
        # 即時資料也補不回這些「今天」bar，因為它們根本沒有機會被merge蓋掉
        # (merge只在同一個ts才會覆蓋，kbars多出來的那幾根ts在Hub裡沒有對應)。
        # 今天完全交給即時Hub負責，Hub沒有的分鐘就是真的還沒有資料。
        self.assertEqual([bar["ts"] for bar in today_bars], [ts(2026, 8, 7, 9, 5)])

    def test_multiday_history_keeps_previous_day_and_live_overrides_today(self):
        result = get_stock_history_bars_5m(
            "2344",
            calendar_days=14,
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.service.api.calls, [("2026-07-25", "2026-08-07")])
        self.assertTrue(result["bootstrap"]["history_ok"])
        self.assertEqual(result["bootstrap"]["source"], "shioaji_kbars_range+realtime_hub")

        bars = result["bars"]
        dates = {datetime.fromtimestamp(bar["ts"] / 1000, TW_TZ).strftime("%Y-%m-%d") for bar in bars}
        self.assertIn("2026-08-06", dates)
        self.assertIn("2026-08-07", dates)
        live = next(bar for bar in bars if bar["ts"] == ts(2026, 8, 7, 9, 5))
        self.assertEqual(live["close"], 111)
        self.assertEqual(live["tick_count"], 9)

    def test_repeated_read_reuses_range_cache(self):
        first = get_stock_history_bars_5m(
            "2344",
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
        )
        second = get_stock_history_bars_5m(
            "2344",
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
        )
        self.assertGreater(first["bar_count"], 0)
        self.assertEqual(first["bar_count"], second["bar_count"])
        self.assertEqual(len(self.service.api.calls), 1)

    def test_busy_shared_broker_budget_returns_live_bars_without_rpc(self):
        self.assertTrue(_history_slots.acquire(blocking=False))
        self.assertTrue(_history_slots.acquire(blocking=False))
        try:
            result = get_stock_history_bars_5m(
                "2344", service=self.service, hub=self.hub, now_ms=self.now_ms,
            )
            self.assertFalse(result["bootstrap"]["history_ok"])
            self.assertEqual(result["bars"][-1]["close"], 111)
            self.assertEqual(self.service.api.calls, [])
        finally:
            _history_slots.release()
            _history_slots.release()
        retry = get_stock_history_bars_5m(
            "2344", service=self.service, hub=self.hub, now_ms=self.now_ms,
        )
        self.assertTrue(retry["bootstrap"]["history_ok"])

    def test_duplicate_request_does_not_wait_for_running_history(self):
        code_lock = _code_lock("2344")
        code_lock.acquire()
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                pending = executor.submit(
                    get_stock_history_bars_5m, "2344",
                    service=self.service, hub=self.hub, now_ms=self.now_ms,
                )
                result = pending.result(timeout=1)
                self.assertEqual(result["bars"][-1]["close"], 111)
                self.assertFalse(result["bootstrap"]["history_ok"])
                self.assertEqual(self.service.api.calls, [])
            finally:
                code_lock.release()

    def test_failed_expanded_history_preserves_known_bars(self):
        first = get_stock_history_bars_5m(
            "2344", calendar_days=3, service=self.service, hub=self.hub, now_ms=self.now_ms,
        )
        with patch.object(self.service.api, "kbars", side_effect=RuntimeError("broker unavailable")):
            failed = get_stock_history_bars_5m(
                "2344", calendar_days=14, service=self.service, hub=self.hub, now_ms=self.now_ms,
            )
        self.assertFalse(failed["bootstrap"]["history_ok"])
        self.assertEqual(failed["bars"], first["bars"])
        self.assertEqual(failed["bootstrap"]["error"], "broker unavailable")

    def test_today_1m_bars_come_only_from_live_hub_not_stale_kbars_snapshot(self):
        result = get_stock_history_bars_1m(
            "2344", calendar_days=5, service=self.service, hub=self.hub, now_ms=self.now_ms,
        )
        today_bars = [
            bar for bar in result["bars"]
            if datetime.fromtimestamp(bar["ts"] / 1000, TW_TZ).strftime("%Y-%m-%d") == "2026-08-07"
        ]
        self.assertEqual([bar["ts"] for bar in today_bars], [ts(2026, 8, 7, 9, 5)])

    def test_multiday_1m_history_keeps_previous_day_and_live_overrides_today(self):
        result = get_stock_history_bars_1m(
            "2344",
            calendar_days=5,
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["bootstrap"]["history_ok"])

        bars = result["bars"]
        dates = {datetime.fromtimestamp(bar["ts"] / 1000, TW_TZ).strftime("%Y-%m-%d") for bar in bars}
        self.assertIn("2026-08-06", dates)
        self.assertIn("2026-08-07", dates)
        live = next(bar for bar in bars if bar["ts"] == ts(2026, 8, 7, 9, 5))
        self.assertEqual(live["close"], 105.5)
        self.assertEqual(live["tick_count"], 7)

    def test_1m_and_5m_share_the_same_kbars_fetch(self):
        five_min = get_stock_history_bars_5m(
            "2344", calendar_days=14, service=self.service, hub=self.hub, now_ms=self.now_ms,
        )
        one_min = get_stock_history_bars_1m(
            "2344", calendar_days=5, service=self.service, hub=self.hub, now_ms=self.now_ms,
        )
        self.assertTrue(five_min["bootstrap"]["history_ok"])
        self.assertTrue(one_min["bootstrap"]["history_ok"])
        # 5分K先抓了較寬的範圍，1分K的請求範圍較窄，應該直接沿用快取，
        # 不會為了1分K再打一次Shioaji kbars。
        self.assertEqual(len(self.service.api.calls), 1)


if __name__ == "__main__":
    unittest.main()
