"""Railway 中途重啟後，今天開盤到 Hub 第一根之前的 5 分 K／1 分 K 要用當日 kbars 補回來。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import stock_history_service as module
from history_quota import HistoryQuotaGate
from stock_history_service import clear_stock_history_cache, get_stock_history_bars_1m, get_stock_history_bars_5m

TW = timezone(timedelta(hours=8))
TODAY = "2026-09-23"      # 週三
YESTERDAY = "2026-09-22"


def ts(day: str, hour: int, minute: int) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=TW).timestamp() * 1000)


def bar(day: str, hour: int, minute: int, close: float = 100.0) -> dict:
    return {"ts": ts(day, hour, minute), "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 10, "tick_count": 1}


class FakeApi:
    """kbars 回今天（到 last_minute 為止）＋昨天整天的 1 分 K；收棒時間表示法跟 Shioaji 一樣。"""

    def __init__(self, last_minute: tuple[int, int] = (11, 39)):
        self.calls: list[tuple[str, str]] = []
        self.error: Exception | None = None
        self.last_minute = last_minute

    def usage(self):
        return {"limit_bytes": 500, "remaining_bytes": 400}

    def kbars(self, *, contract, start: str, end: str):
        self.calls.append((start, end))
        if self.error is not None:
            raise self.error
        closes: list[datetime] = []
        for day, until in ((YESTERDAY, (13, 30)), (TODAY, self.last_minute)):
            if not (start <= day <= end):
                continue
            moment = datetime.fromisoformat(f"{day}T09:01:00").replace(tzinfo=TW)
            stop = datetime.fromisoformat(f"{day}T{until[0]:02d}:{until[1]:02d}:00").replace(tzinfo=TW)
            while moment <= stop:
                closes.append(moment)
                moment += timedelta(minutes=1)
        n = len(closes)
        return {"ts": closes, "Open": [50.0] * n, "High": [51.0] * n, "Low": [49.0] * n, "Close": [50.5] * n, "Volume": [7] * n}


class FakeHub:
    def __init__(self, bars_5m: list[dict], bars_1m: list[dict]):
        self._5m = bars_5m
        self._1m = bars_1m

    def get_live_bars(self, code):
        return list(self._5m)

    def get_live_bars_1m(self, code):
        return list(self._1m)


def service_with(api) -> SimpleNamespace:
    svc = SimpleNamespace(api=api, state=SimpleNamespace(logged_in=True))
    svc.ensure_stock_subscriptions = lambda codes: {"requested": list(codes)}
    svc._resolve_stock_contract = lambda code: object()
    return svc


def live_after_restart() -> FakeHub:
    """Hub 11:25 重啟後才開始累積：5 分 K 從 11:25、1 分 K 從 11:27。收盤價 200 好跟 kbars 的 50.5 區分。"""
    bars_5m = [bar(TODAY, 11, m, 200.0) for m in (25, 30, 35)]
    bars_1m = [bar(TODAY, 11, m, 200.0) for m in range(27, 40)]
    return FakeHub(bars_5m, bars_1m)


class TodayGapTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_stock_history_cache()
        self.quota = patch.object(module, "history_quota", HistoryQuotaGate())
        self.quota.start()
        self.market = patch.object(module, "stock_market", lambda code: "TSE")
        self.market.start()
        self.chain = patch.object(module, "fetch_minute_bars_chain", lambda *a, **k: ([], None))
        self.chain.start()
        self.now_ms = ts(TODAY, 11, 40)
        self.clock = [1000.0]

    def tearDown(self) -> None:
        self.chain.stop()
        self.market.stop()
        self.quota.stop()
        clear_stock_history_cache()

    def fetch_5m(self, api, hub, now_ms=None):
        return get_stock_history_bars_5m("8054", calendar_days=3, service=service_with(api), hub=hub,
                                         now_ms=now_ms or self.now_ms, monotonic_fn=lambda: self.clock[0])

    def fetch_1m(self, api, hub, now_ms=None):
        return get_stock_history_bars_1m("8054", calendar_days=3, service=service_with(api), hub=hub,
                                         now_ms=now_ms or self.now_ms, monotonic_fn=lambda: self.clock[0])

    def test_gap_before_first_live_bar_is_filled_from_today_kbars(self) -> None:
        api = FakeApi()
        result = self.fetch_5m(api, live_after_restart())
        today = [b for b in result["bars"] if module.taipei_trade_date(b["ts"]) == TODAY]
        # 09:00 … 11:20 共 29 根來自 kbars，11:25 起 3 根來自 Hub
        self.assertEqual(today[0]["ts"], ts(TODAY, 9, 0))
        self.assertEqual(len(today), 29 + 3)
        self.assertEqual([b["close"] for b in today[:29]], [50.5] * 29)
        self.assertEqual([b["close"] for b in today[29:]], [200.0] * 3)  # Hub 有的永遠以 Hub 為準
        self.assertEqual(result["bootstrap"]["today_gap"]["filled"], 29)
        self.assertTrue(result["bootstrap"]["today_gap"]["ok"])
        self.assertEqual(result["bootstrap"]["today_gap"]["live_first"], ts(TODAY, 11, 25))
        # 昨天整天照舊來自多日歷史
        yesterday = [b for b in result["bars"] if module.taipei_trade_date(b["ts"]) == YESTERDAY]
        self.assertEqual(len(yesterday), 54)
        self.assertIn((TODAY, TODAY), api.calls)

    def test_gap_is_fetched_once_and_reused_for_the_day(self) -> None:
        api = FakeApi()
        hub = live_after_restart()
        self.fetch_5m(api, hub)
        self.fetch_5m(api, hub)
        self.fetch_1m(api, hub)
        self.assertEqual(api.calls.count((TODAY, TODAY)), 1)

    def test_no_gap_fetch_when_hub_has_bars_from_the_open(self) -> None:
        api = FakeApi()
        hub = FakeHub([bar(TODAY, 9, 0, 200.0), bar(TODAY, 9, 5, 200.0)], [bar(TODAY, 9, 0, 200.0)])
        result = self.fetch_5m(api, hub)
        self.assertNotIn((TODAY, TODAY), api.calls)
        self.assertIsNone(result["bootstrap"]["today_gap"])

    def test_no_gap_fetch_after_close_because_history_already_includes_today(self) -> None:
        api = FakeApi(last_minute=(13, 30))
        result = self.fetch_5m(api, live_after_restart(), now_ms=ts(TODAY, 14, 0))
        self.assertNotIn((TODAY, TODAY), api.calls)
        self.assertIsNone(result["bootstrap"]["today_gap"])
        today = [b for b in result["bars"] if module.taipei_trade_date(b["ts"]) == TODAY]
        self.assertEqual(today[0]["ts"], ts(TODAY, 9, 0))

    def test_no_gap_fetch_right_after_the_open(self) -> None:
        api = FakeApi(last_minute=(9, 3))
        result = self.fetch_5m(api, FakeHub([], []), now_ms=ts(TODAY, 9, 4))
        self.assertNotIn((TODAY, TODAY), api.calls)
        self.assertIsNone(result["bootstrap"]["today_gap"])

    def test_hub_without_any_bars_today_gets_all_of_today_from_kbars(self) -> None:
        api = FakeApi()
        result = self.fetch_5m(api, FakeHub([], []))
        today = [b for b in result["bars"] if module.taipei_trade_date(b["ts"]) == TODAY]
        self.assertEqual(today[0]["ts"], ts(TODAY, 9, 0))
        self.assertEqual(today[-1]["ts"], ts(TODAY, 11, 35))
        self.assertEqual(result["bootstrap"]["today_gap"]["filled"], len(today))

    def test_1m_path_fills_gap_before_first_live_minute(self) -> None:
        api = FakeApi()
        result = self.fetch_1m(api, live_after_restart())
        today = [b for b in result["bars"] if module.taipei_trade_date(b["ts"]) == TODAY]
        self.assertEqual(today[0]["ts"], ts(TODAY, 9, 0))
        first_live = ts(TODAY, 11, 27)
        from_kbars = [b for b in today if b["ts"] < first_live]
        self.assertEqual(len(from_kbars), 147)  # 09:00 … 11:26
        self.assertTrue(all(b["close"] == 50.5 for b in from_kbars))
        self.assertTrue(all(b["close"] == 200.0 for b in today if b["ts"] >= first_live))
        self.assertEqual(result["bootstrap"]["today_gap"]["filled"], 147)

    def test_kbars_failure_keeps_history_and_live_and_retries_after_cooldown(self) -> None:
        api = FakeApi()
        api.error = RuntimeError("shioaji timeout")
        hub = live_after_restart()
        result = self.fetch_5m(api, hub)
        today = [b for b in result["bars"] if module.taipei_trade_date(b["ts"]) == TODAY]
        self.assertEqual([b["close"] for b in today], [200.0] * 3)
        self.assertFalse(result["bootstrap"]["today_gap"]["ok"])
        self.assertIn("timeout", result["bootstrap"]["today_gap"]["error"])
        gap_calls = api.calls.count((TODAY, TODAY))
        self.fetch_5m(api, hub)  # 冷卻中不重打
        self.assertEqual(api.calls.count((TODAY, TODAY)), gap_calls)
        api.error = None
        self.clock[0] += module.GAP_RETRY_SECONDS + 1
        result = self.fetch_5m(api, hub)
        self.assertEqual(api.calls.count((TODAY, TODAY)), gap_calls + 1)
        self.assertEqual(result["bootstrap"]["today_gap"]["filled"], 29)

    def test_incomplete_gap_is_refetched_after_cooldown_until_it_reaches_the_first_live_bar(self) -> None:
        api = FakeApi(last_minute=(11, 9))  # kbars 落後：只到 11:09
        hub = live_after_restart()
        result = self.fetch_5m(api, hub)
        self.assertEqual(result["bootstrap"]["today_gap"]["filled"], 26)  # 09:00 … 11:05
        api.last_minute = (11, 39)
        self.fetch_5m(api, hub)  # 冷卻中：沿用
        self.assertEqual(api.calls.count((TODAY, TODAY)), 1)
        self.clock[0] += module.GAP_RETRY_SECONDS + 1
        result = self.fetch_5m(api, hub)
        self.assertEqual(api.calls.count((TODAY, TODAY)), 2)
        self.assertEqual(result["bootstrap"]["today_gap"]["filled"], 29)
        self.clock[0] += module.GAP_RETRY_SECONDS + 1
        self.fetch_5m(api, hub)  # 補齊了就不再打
        self.assertEqual(api.calls.count((TODAY, TODAY)), 2)

    def test_quota_exhausted_uses_fallback_chain_for_the_gap(self) -> None:
        class ExhaustedApi(FakeApi):
            def usage(self):
                return {"limit_bytes": 500, "remaining_bytes": 0}

        chain_bars = [bar(TODAY, 9, m, 77.0) for m in range(0, 60)]
        with patch.object(module, "fetch_minute_bars_chain", lambda code, start, end, *, market=None, fetcher=None: (chain_bars, "finmind")):
            result = self.fetch_5m(ExhaustedApi(), live_after_restart())
        today = [b for b in result["bars"] if module.taipei_trade_date(b["ts"]) == TODAY]
        self.assertEqual(today[0]["ts"], ts(TODAY, 9, 0))
        self.assertEqual(result["bootstrap"]["today_gap"]["source"], "finmind")
        self.assertEqual(result["bootstrap"]["today_gap"]["filled"], 12)  # 09:00 … 09:55


if __name__ == "__main__":
    unittest.main()
