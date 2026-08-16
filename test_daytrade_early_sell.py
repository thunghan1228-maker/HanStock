from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import database
from daytrade_early_sell import collect_early_sell_signals
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

    def test_uses_previous_net_amount_and_allows_later_five_minute_repeat(self):
        service = FakeService()
        hub = FakeHub({
            "2330": [
                {"ts": ts(9, 0), "close": 99, "main_sell_amount": 3_000_000},
                {"ts": ts(9, 4), "close": 98, "main_sell_amount": 1_000_000},
            ],
        })
        now = datetime(2026, 8, 17, 9, 4, 30, tzinfo=TW_TZ)
        first = collect_early_sell_signals(service, hub, now)
        self.assertEqual(len(first["inserted"]), 1)
        self.assertIn("前日淨額 8000000", first["inserted"][0]["note"])
        self.assertIn("比例 50.0%", first["inserted"][0]["note"])

        # 同一根 5 分 K 重跑不再通知。
        repeated = collect_early_sell_signals(service, hub, now)
        self.assertEqual(repeated["inserted"], [])

        # 下一根 5 分 K 又有新的大單賣出，可再次通知同一股票。
        hub.bars["2330"].append(
            {"ts": ts(9, 6), "close": 97, "main_sell_amount": 1_000_000},
        )
        later = collect_early_sell_signals(
            service, hub, datetime(2026, 8, 17, 9, 6, 20, tzinfo=TW_TZ)
        )
        self.assertEqual(len(later["inserted"]), 1)
        self.assertIn("比例 62.5%", later["inserted"][0]["note"])

    def test_does_not_use_previous_buy_amount_or_nine_thirty_bar(self):
        service = FakeService()
        hub = FakeHub({
            "2330": [
                # 3.9m 已超過前日買進 10m 的錯誤 39% 判法，但未達前日淨額 8m 的 50%。
                {"ts": ts(9, 29), "close": 99, "main_sell_amount": 3_900_000},
                # 09:30 起的成交不屬於使用者指定的九點半之前。
                {"ts": ts(9, 30), "close": 98, "main_sell_amount": 5_000_000},
            ],
        })
        result = collect_early_sell_signals(
            service, hub, datetime(2026, 8, 17, 9, 30, 20, tzinfo=TW_TZ)
        )
        self.assertEqual(result["inserted"], [])


if __name__ == "__main__":
    unittest.main()
