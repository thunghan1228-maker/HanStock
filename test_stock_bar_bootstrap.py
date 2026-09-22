from __future__ import annotations

import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from otc_index import TW_TZ
from stock_bar_bootstrap import (
    _historical_tick_metrics,
    _safe_bar,
    clear_stock_bar_bootstrap_cache,
    get_resilient_stock_bars,
    repair_recent_stock_bars_once,
)


def ts(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=TW_TZ).timestamp() * 1000)


class FakeApi:
    def __init__(self) -> None:
        self.kbars_calls = 0
        self.ticks_calls = 0
        self.kbars_dates: list[tuple[str, str]] = []
        self.ticks_dates: list[str] = []
        self.kbars_error: Exception | None = None

    def kbars(self, *, contract, start: str, end: str):
        self.kbars_calls += 1
        self.kbars_dates.append((start, end))
        if self.kbars_error is not None:
            raise self.kbars_error
        # Shioaji KBars 的 ts 是「收棒時間」；09:01 代表 09:00~09:01。
        closes = [
            datetime(2026, 8, 7, 9, 1, tzinfo=TW_TZ),
            datetime(2026, 8, 7, 9, 2, tzinfo=TW_TZ),
            datetime(2026, 8, 7, 9, 3, tzinfo=TW_TZ),
            datetime(2026, 8, 7, 9, 4, tzinfo=TW_TZ),
            datetime(2026, 8, 7, 9, 5, tzinfo=TW_TZ),
            datetime(2026, 8, 7, 9, 6, tzinfo=TW_TZ),
        ]
        return {
            "ts": closes,
            "Open": [100, 101, 102, 103, 104, 105],
            "High": [101, 102, 103, 104, 105, 106],
            "Low": [99, 100, 101, 102, 103, 104],
            "Close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
            "Volume": [10, 20, 30, 40, 50, 60],
        }

    def ticks(self, *, contract, date: str, **_kwargs):
        self.ticks_calls += 1
        self.ticks_dates.append(date)
        return {
            "ts": [
                datetime(2026, 8, 7, 9, 0, 10, tzinfo=TW_TZ),
                datetime(2026, 8, 7, 9, 0, 20, tzinfo=TW_TZ),
                datetime(2026, 8, 7, 9, 1, 10, tzinfo=TW_TZ),
                datetime(2026, 8, 7, 9, 2, 10, tzinfo=TW_TZ),
            ],
            "close": [100, 100, 600, 102],
            "volume": [25, 30, 2, 3],
            "tick_type": [1, 2, 1, 0],
            "simtrade": [0, 0, 0, 0],
        }


class FakeService:
    def __init__(self) -> None:
        self.api = FakeApi()
        self.state = SimpleNamespace(logged_in=True)
        self.subscription_calls: list[list[str]] = []
        self.contract = object()
        self.recovery_reasons: list[str] = []

    def ensure_stock_subscriptions(self, codes):
        codes = list(codes)
        self.subscription_calls.append(codes)
        return {
            "requested": codes,
            "newly_subscribed": codes if len(self.subscription_calls) == 1 else [],
            "already_subscribed": [] if len(self.subscription_calls) == 1 else codes,
            "failed": {},
        }

    def _resolve_stock_contract(self, code: str):
        return self.contract if code == "2344" else None

    def recover_transient_p2p_session(self, reason: str):
        self.recovery_reasons.append(reason)


class FakeHub:
    def __init__(self) -> None:
        self.live_1m = {
            "2344": [
                # 舊交易日資料必須被排除。
                {
                    "ts": ts(2026, 8, 6, 9, 0),
                    "open": 80,
                    "high": 81,
                    "low": 79,
                    "close": 80.5,
                    "volume": 1,
                    "tick_count": 1,
                },
                # 09:05 與歷史重複；live 必須覆蓋 history。
                {
                    "ts": ts(2026, 8, 7, 9, 5),
                    "open": 105,
                    "high": 111,
                    "low": 104,
                    "close": 110,
                    "volume": 99,
                    "tick_count": 9,
                },
                # 新的即時分鐘。
                {
                    "ts": ts(2026, 8, 7, 9, 6),
                    "open": 110,
                    "high": 112,
                    "low": 109,
                    "close": 111,
                    "volume": 12,
                    "tick_count": 4,
                },
            ]
        }
        self.live_5m = {
            "2344": [
                {
                    "ts": ts(2026, 8, 7, 9, 5),
                    "open": 105,
                    "high": 112,
                    "low": 104,
                    "close": 111,
                    "volume": 111,
                    "tick_count": 13,
                }
            ]
        }

    def get_live_bars_1m(self, code: str):
        return list(self.live_1m.get(code, []))

    def get_live_bars(self, code: str):
        return list(self.live_5m.get(code, []))


class StockBarBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_stock_bar_bootstrap_cache()
        self.service = FakeService()
        self.hub = FakeHub()
        self.now_ms = ts(2026, 8, 7, 9, 7)
        # 這裡的 tick 樣本是照「單筆 ≥ 20 張或金額 ≥ 100 萬」設計的；主力大單預設已改成只看
        # 張數（跟另一台工具對照後），這組測試只驗證回補的管線，門檻沿用舊值就好。
        import market_data_hub

        self.amount_patch = patch.object(market_data_hub, "MAIN_FORCE_MIN_AMOUNT", 1_000_000.0)
        self.amount_patch.start()

    def tearDown(self) -> None:
        self.amount_patch.stop()

    def test_history_concurrency_is_bounded_and_live_data_does_not_wait(self):
        release = threading.Event()
        both_started = threading.Event()
        active = []
        guard = threading.Lock()
        original = self.service.api.kbars
        self.service._resolve_stock_contract = lambda code: object()
        def slow_kbars(**kwargs):
            with guard:
                active.append(1)
                if len(active) == 2:
                    both_started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test did not release history")
            return original(**kwargs)
        def read(code):
            return get_resilient_stock_bars(code, "1m", service=self.service,
                                           hub=self.hub, now_ms=self.now_ms)
        with patch.object(self.service.api, "kbars", side_effect=slow_kbars):
            with ThreadPoolExecutor(max_workers=4) as pool:
                first = pool.submit(read, "2344")
                second = pool.submit(read, "2330")
                try:
                    self.assertTrue(both_started.wait(timeout=1))
                    # A different stock cannot start a third SDK call, and a duplicate
                    # stock must return the live bars without waiting for its lock.
                    third = pool.submit(read, "2408").result(timeout=0.5)
                    duplicate = pool.submit(read, "2344").result(timeout=0.5)
                    self.assertFalse(third["bootstrap"]["history_ok"])
                    self.assertEqual(duplicate["bar_count"], 2)
                    self.assertEqual(len(active), 2)
                finally:
                    release.set()
                self.assertTrue(first.result(timeout=2)["bootstrap"]["history_ok"])
                self.assertTrue(second.result(timeout=2)["bootstrap"]["history_ok"])

    def test_session_failure_cools_down_other_stock_history_requests(self):
        self.service._resolve_stock_contract = lambda code: object()
        self.service.api.kbars_error = RuntimeError("NotReady SessionNotEstablished")
        for code, now in [("2344", 100), ("2330", 110)]:
            get_resilient_stock_bars(code, "1m", service=self.service,
                                    hub=self.hub, now_ms=self.now_ms,
                                    monotonic_fn=lambda: now)
        self.assertEqual(self.service.api.kbars_calls, 1)
        self.assertEqual(len(self.service.recovery_reasons), 1)
        get_resilient_stock_bars("2330", "1m", service=self.service,
                                hub=self.hub, now_ms=self.now_ms,
                                monotonic_fn=lambda: 131)
        self.assertEqual(self.service.api.kbars_calls, 2)

    def test_failed_refresh_preserves_completed_history(self):
        first = get_resilient_stock_bars("2344", "1m", service=self.service,
                                        hub=self.hub, now_ms=self.now_ms,
                                        monotonic_fn=lambda: 100)
        self.service.api.kbars_error = RuntimeError("temporary transport failure")
        repair_recent_stock_bars_once(service=self.service, now_ms=self.now_ms,
                                      monotonic_fn=lambda: 281)
        after = get_resilient_stock_bars("2344", "1m", service=self.service,
                                        hub=self.hub, now_ms=self.now_ms,
                                        monotonic_fn=lambda: 282)
        self.assertFalse(after["bootstrap"]["history_ok"])
        self.assertEqual(after["bars"], first["bars"])

    def test_one_minute_bootstrap_subscribes_and_live_overrides_history(self):
        result = get_resilient_stock_bars(
            "2344",
            "1m",
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.service.subscription_calls, [["2344"]])
        self.assertEqual(self.service.api.kbars_calls, 1)
        self.assertEqual(self.service.api.ticks_calls, 1)
        self.assertEqual(result["bootstrap"]["history_1m"], 6)
        self.assertTrue(result["bootstrap"]["history_ok"])
        self.assertTrue(result["bootstrap"]["main_force_history_ok"])

        bars = result["bars"]
        self.assertEqual([bar["ts"] for bar in bars], [ts(2026, 8, 7, 9, minute) for minute in range(7)])
        bar_0905 = next(bar for bar in bars if bar["ts"] == ts(2026, 8, 7, 9, 5))
        self.assertEqual(bar_0905["close"], 110)
        self.assertEqual(bar_0905["tick_count"], 9)
        bar_0900 = next(bar for bar in bars if bar["ts"] == ts(2026, 8, 7, 9, 0))
        self.assertEqual(bar_0900["main_buy_volume"], 25)
        self.assertEqual(bar_0900["main_sell_volume"], 30)
        self.assertEqual(bar_0900["main_net_volume"], -5)
        self.assertEqual(bar_0900["main_buy_amount"], 2_500_000)
        self.assertEqual(bar_0900["main_sell_amount"], 3_000_000)
        self.assertEqual(bar_0900["main_net_amount"], -500_000)
        self.assertTrue(bar_0900["main_force_available"])
        bar_0901 = next(bar for bar in bars if bar["ts"] == ts(2026, 8, 7, 9, 1))
        self.assertEqual(bar_0901["main_buy_volume"], 2, "金額超過 100 萬的小張數交易也應列為大單")
        self.assertEqual(bar_0901["main_buy_amount"], 1_200_000)
        self.assertNotIn(ts(2026, 8, 6, 9, 0), [bar["ts"] for bar in bars])

    def test_five_minute_reuses_history_cache_and_keeps_live_current_bucket(self):
        first = get_resilient_stock_bars(
            "2344",
            "1m",
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
        )
        second = get_resilient_stock_bars(
            "2344",
            "5m",
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
        )

        self.assertGreater(first["bar_count"], 0)
        self.assertEqual(self.service.api.kbars_calls, 1, "1m/5m 同時讀取不可重複打 kbars()")
        self.assertEqual(self.service.api.ticks_calls, 1, "1m/5m 同時讀取不可重複打 ticks()")
        self.assertEqual(second["bootstrap"]["history_5m"], 1)
        self.assertEqual([bar["ts"] for bar in second["bars"]], [
            ts(2026, 8, 7, 9, 0),
            ts(2026, 8, 7, 9, 5),
        ])
        self.assertEqual(second["bars"][-1]["close"], 111)
        first_five = second["bars"][0]
        self.assertEqual(first_five["main_buy_volume"], 27)
        self.assertEqual(first_five["main_sell_volume"], 30)
        self.assertEqual(first_five["main_net_volume"], -3)
        self.assertEqual(first_five["main_buy_amount"], 3_700_000)
        self.assertEqual(first_five["main_sell_amount"], 3_000_000)
        self.assertEqual(first_five["main_net_amount"], 700_000)
        self.assertTrue(first_five["main_force_available"])

    def test_weekend_uses_previous_friday_and_restores_main_force_amounts(self):
        self.hub.live_1m = {}
        self.hub.live_5m = {}

        result = get_resilient_stock_bars(
            "2344",
            "5m",
            service=self.service,
            hub=self.hub,
            now_ms=ts(2026, 8, 9, 12, 0),
        )

        self.assertEqual(result["bootstrap"]["trade_date"], "2026-08-07")
        self.assertEqual(self.service.api.kbars_dates, [("2026-08-07", "2026-08-07")])
        self.assertEqual(self.service.api.ticks_dates, ["2026-08-07"])
        self.assertTrue(result["bootstrap"]["main_force_history_ok"])
        self.assertEqual(result["bars"][0]["main_buy_amount"], 3_700_000)
        self.assertEqual(result["bars"][0]["main_sell_amount"], 3_000_000)
        self.assertEqual(result["bars"][0]["main_net_amount"], 700_000)

    def test_failed_contract_returns_live_data_and_throttles_bootstrap_retry(self):
        self.service._resolve_stock_contract = lambda code: None
        self.hub.live_1m["9999"] = [{
            "ts": ts(2026, 8, 7, 9, 6),
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 3,
            "tick_count": 2,
        }]

        first = get_resilient_stock_bars(
            "9999",
            "1m",
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
            monotonic_fn=lambda: 100.0,
        )
        second = get_resilient_stock_bars(
            "9999",
            "1m",
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
            monotonic_fn=lambda: 110.0,
        )

        self.assertFalse(first["bootstrap"]["history_ok"])
        self.assertEqual(first["bar_count"], 1)
        self.assertEqual(second["bar_count"], 1)
        self.assertIn("找不到股票合約", first["bootstrap"]["error"])

    def test_session_failure_triggers_reconnect_then_background_patrol_backfills(self):
        self.service.api.kbars_error = RuntimeError(
            "NotReady SessionNotEstablished Unable to wait for session"
        )
        first = get_resilient_stock_bars(
            "2344",
            "1m",
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
            monotonic_fn=lambda: 100.0,
        )
        self.assertFalse(first["bootstrap"]["history_ok"])
        self.assertTrue(first["bootstrap"]["auto_repair"]["waiting"])
        self.assertEqual(len(self.service.recovery_reasons), 1)
        self.assertIn("自動復原", first["bootstrap"]["error"])

        self.service.api.kbars_error = None
        repaired = repair_recent_stock_bars_once(
            service=self.service,
            now_ms=self.now_ms,
            monotonic_fn=lambda: 131.0,
        )
        self.assertEqual(repaired["repairedCodes"], ["2344"])
        after = get_resilient_stock_bars(
            "2344",
            "1m",
            service=self.service,
            hub=self.hub,
            now_ms=self.now_ms,
            monotonic_fn=lambda: 132.0,
        )
        self.assertTrue(after["bootstrap"]["history_ok"])
        self.assertEqual(after["bar_count"], 7)


TW = timezone(timedelta(hours=8))


def tw_dt(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 9, 21, hour, minute, second, tzinfo=TW)


class HistoricalTickMetricsTotalAmountTests(unittest.TestCase):
    """大戶力%的分母(累計成交額)之前只在即時tick路徑(market_data_hub.py)
    才會算，歷史回補路徑(這個函式)完全沒有算，導致熱門股只要是透過「打開
    K線圖回補歷史資料」拿到主力資料，total_amount永遠是0，卡在門檻進不去
    強多/強空分類。"""

    def test_total_amount_accumulates_every_tick_not_just_main_force_ones(self) -> None:
        # tick1量小(5張)、金額落回退(500,000)，不到主力大單門檻，但還是要
        # 算進累計成交額；tick2量夠(30張)才算主力大單。
        ticks = {
            "ts": [tw_dt(9, 0, 10), tw_dt(9, 0, 40)],
            "close": [100.0, 100.0],
            "volume": [5, 30],
            "tick_type": [0, 1],
            "amount": [0, 3_000_000],
        }
        metrics = _historical_tick_metrics(ticks, trade_date="2026-09-21")
        minute_ts = int(tw_dt(9, 0, 0).timestamp() * 1000)
        row = metrics[minute_ts]
        # 500,000(tick1的close*volume*1000回退值) + 3,000,000(tick2) = 3,500,000。
        self.assertEqual(row["total_amount"], 3_500_000)
        # 主力大單統計不能被我改動的地方影響：只有tick2(30張)算主力大單。
        self.assertEqual(row["main_buy_volume"], 30)
        self.assertEqual(row["main_buy_amount"], 3_000_000)

    def test_total_amount_is_cumulative_across_minute_buckets_not_per_minute(self) -> None:
        ticks = {
            "ts": [tw_dt(9, 0, 10), tw_dt(9, 1, 5)],
            "close": [100.0, 101.0],
            "volume": [30, 10],
            "tick_type": [1, 2],
            "amount": [3_000_000, 1_010_000],
        }
        metrics = _historical_tick_metrics(ticks, trade_date="2026-09-21")
        minute_0 = int(tw_dt(9, 0, 0).timestamp() * 1000)
        minute_1 = int(tw_dt(9, 1, 0).timestamp() * 1000)
        self.assertEqual(metrics[minute_0]["total_amount"], 3_000_000)
        # 09:01這根要是「累計到09:01為止」= 3,000,000 + 1,010,000，
        # 不是只有這一分鐘自己的1,010,000。
        self.assertEqual(metrics[minute_1]["total_amount"], 4_010_000)

    def test_total_amount_survives_out_of_order_ticks(self) -> None:
        # Shioaji歷史ticks() API不保證回傳順序；就算輸入順序是反的，
        # 累計結果也要跟照時間正序輸入時一樣，不能依賴輸入本身有序。
        ticks = {
            "ts": [tw_dt(9, 1, 5), tw_dt(9, 0, 10)],
            "close": [101.0, 100.0],
            "volume": [10, 30],
            "tick_type": [2, 1],
            "amount": [1_010_000, 3_000_000],
        }
        metrics = _historical_tick_metrics(ticks, trade_date="2026-09-21")
        minute_0 = int(tw_dt(9, 0, 0).timestamp() * 1000)
        minute_1 = int(tw_dt(9, 1, 0).timestamp() * 1000)
        self.assertEqual(metrics[minute_0]["total_amount"], 3_000_000)
        self.assertEqual(metrics[minute_1]["total_amount"], 4_010_000)


class SafeBarTotalAmountTests(unittest.TestCase):
    """_safe_bar是_merge_bars(合併live/history bar後存檔前)的過濾器；
    之前它的欄位allowlist漏了total_amount，即使live bar本身有正確的
    累計成交額，經過這裡也會被拿掉，導致存進main_force_bars的還是0。"""

    def test_total_amount_is_kept_when_present(self) -> None:
        raw = {
            "ts": 1_700_000_000_000, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 10, "tick_count": 1,
            "main_force_available": True, "total_amount": 123_456_789.0,
        }
        bar = _safe_bar(raw)
        self.assertIsNotNone(bar)
        self.assertEqual(bar["total_amount"], 123_456_789)

    def test_total_amount_absent_when_not_in_raw(self) -> None:
        raw = {
            "ts": 1_700_000_000_000, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 10, "tick_count": 1,
        }
        bar = _safe_bar(raw)
        self.assertIsNotNone(bar)
        self.assertNotIn("total_amount", bar)

    def test_negative_total_amount_clamped_to_zero(self) -> None:
        raw = {
            "ts": 1_700_000_000_000, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 10, "tick_count": 1,
            "total_amount": -5.0,
        }
        bar = _safe_bar(raw)
        self.assertEqual(bar["total_amount"], 0)


if __name__ == "__main__":
    unittest.main()
