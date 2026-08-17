from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import database
from daytrade_early_sell import (
    collect_early_sell_signals,
    early_sell_signal_snapshot,
    historical_early_sell_signals_for_ticks,
)
from daytrade_flow_store import save_daytrade_rows
from otc_index import TW_TZ


class FakeService:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def ensure_stock_subscriptions(self, codes):
        self.requested = list(codes)
        return {"active_count": len(self.requested), "failed": {}}


class FakeHub:
    def __init__(self, bars):
        self.bars = bars

    def get_live_bars_1m(self, ticker):
        return list(self.bars.get(ticker, []))


def ts(hour: int, minute: int) -> int:
    return int(datetime(2026, 8, 17, hour, minute, tzinfo=TW_TZ).timestamp() * 1000)


def saved_row(ticker: str = "2330") -> dict:
    return {
        "ticker": ticker,
        "trade_date": "2026-08-14",
        "name": "台積電",
        "market": "上市",
        "category": "強勢大單",
        "close_price": 100,
        "large_buy_amount": 10_000_000,
        "large_sell_amount": 2_000_000,
        "total_turnover_amount": 50_000_000,
        "suspicion_score": 70,
    }


class DaytradeEarlySellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.temp.name) / "hanstock.db"
        save_daytrade_rows([saved_row()])

    def tearDown(self):
        database.DATABASE_PATH = self.old_path
        self.temp.cleanup()

    def test_uses_estimated_next_day_sell_pressure_and_allows_next_minute_repeat(self):
        service = FakeService()
        hub = FakeHub({
            "2330": [
                {"ts": ts(9, 1), "close": 99, "main_sell_amount": 2_000_000},
                {"ts": ts(9, 2), "close": 98, "main_sell_amount": 500_000},
            ],
        })
        now = datetime(2026, 8, 17, 9, 2, 30, tzinfo=TW_TZ)
        first = collect_early_sell_signals(service, hub, now)
        self.assertEqual(len(first["inserted"]), 1)
        self.assertEqual(first["inserted"][0]["barTs"], ts(9, 2))
        self.assertIn("前日預估隔日賣壓 500.0 萬", first["inserted"][0]["note"])
        self.assertIn("比例 50.0%", first["inserted"][0]["note"])

        # 同一分鐘重跑不再通知。
        repeated = collect_early_sell_signals(service, hub, now)
        self.assertEqual(repeated["inserted"], [])

        # 不必等五分整點；下一分鐘又有新的大單賣出即可再次通知同一股票。
        hub.bars["2330"].append(
            {"ts": ts(9, 3), "close": 97, "main_sell_amount": 1_000_000},
        )
        later = collect_early_sell_signals(
            service, hub, datetime(2026, 8, 17, 9, 3, 20, tzinfo=TW_TZ)
        )
        self.assertEqual(len(later["inserted"]), 1)
        self.assertEqual(later["inserted"][0]["barTs"], ts(9, 3))
        self.assertIn("比例 70.0%", later["inserted"][0]["note"])

        snapshot = early_sell_signal_snapshot(now, limit=100, service=service)
        self.assertEqual(snapshot["window"], "09:00～13:30")
        self.assertEqual(snapshot["signals"][0]["barTs"], ts(9, 3))
        self.assertEqual(snapshot["signals"][1]["barTs"], ts(9, 2))

    def test_includes_thirteen_thirty_but_excludes_thirteen_thirty_one(self):
        service = FakeService()
        hub = FakeHub({
            "2330": [
                # 前日預估隔日賣壓為 5m，13:29 累計 2m 尚未達 50%。
                {"ts": ts(13, 29), "close": 99, "main_sell_amount": 2_000_000},
                # 13:30 這一分鐘包含在使用者指定的監控區間內。
                {"ts": ts(13, 30), "close": 98, "main_sell_amount": 500_000},
            ],
        })
        result = collect_early_sell_signals(
            service, hub, datetime(2026, 8, 17, 13, 30, 20, tzinfo=TW_TZ)
        )
        self.assertTrue(result["inWindow"])
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(result["inserted"][0]["barTs"], ts(13, 30))
        self.assertIn("比例 50.0%", result["inserted"][0]["note"])

        after_close = collect_early_sell_signals(
            service, hub, datetime(2026, 8, 17, 13, 31, 0, tzinfo=TW_TZ)
        )
        self.assertFalse(after_close["inWindow"])
        self.assertEqual(after_close["inserted"], [])

    def test_historical_demo_replays_actual_ticks_without_saving_official_signal(self):
        row = saved_row()
        ticks = {
            "ts": [
                datetime(2026, 8, 17, 9, 1),
                datetime(2026, 8, 17, 9, 2),
                datetime(2026, 8, 17, 9, 3),
                datetime(2026, 8, 17, 13, 30),
                datetime(2026, 8, 17, 13, 31),
            ],
            "close": [99, 98, 97, 96, 95],
            "volume": [20, 20, 20, 20, 20],
            "amount": [2_000_000, 500_000, 1_000_000, 500_000, 5_000_000],
            "tick_type": [2, 2, 2, 2, 2],
            "simtrade": [False, False, False, False, False],
        }
        signals = historical_early_sell_signals_for_ticks(ticks, row, "2026-08-17")
        self.assertEqual(len(signals), 3)
        self.assertEqual(signals[0]["barTs"], ts(9, 2))
        self.assertIn("比例 50.0%", signals[0]["note"])
        self.assertEqual(signals[-1]["barTs"], ts(13, 30))
        self.assertTrue(signals[-1]["demo"])


if __name__ == "__main__":
    unittest.main()
