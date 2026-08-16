from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from otc_index import TW_TZ
from stock_bar_bootstrap import clear_stock_bar_bootstrap_cache, get_resilient_stock_bars


def ts(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=TW_TZ).timestamp() * 1000)


class FakeApi:
    def __init__(self) -> None:
        self.kbars_calls = 0
        self.ticks_calls = 0
        self.kbars_dates: list[tuple[str, str]] = []
        self.ticks_dates: list[str] = []

    def kbars(self, *, contract, start: str, end: str):
        self.kbars_calls += 1
        self.kbars_dates.append((start, end))
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


if __name__ == "__main__":
    unittest.main()
