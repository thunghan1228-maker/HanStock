from __future__ import annotations

import unittest
from datetime import datetime

from daytrade_flow import latest_completed_trade_date, summarize_historical_ticks
from otc_index import TW_TZ


class DaytradeFlowTests(unittest.TestCase):
    def test_weekend_defaults_to_friday(self):
        saturday = datetime(2026, 8, 15, 10, 0, tzinfo=TW_TZ)
        self.assertEqual(latest_completed_trade_date(saturday), "2026-08-14")

    def test_historical_ticks_are_aggregated_to_frontend_amount_fields(self):
        ticks = {
            "ts": [
                datetime(2026, 8, 14, 9, 0, 10, tzinfo=TW_TZ),
                datetime(2026, 8, 14, 13, 5, 0, tzinfo=TW_TZ),
                datetime(2026, 8, 14, 13, 10, 0, tzinfo=TW_TZ),
                datetime(2026, 8, 15, 9, 0, 0, tzinfo=TW_TZ),
            ],
            "close": [100, 102, 103, 99],
            "volume": [25, 30, 20, 99],
            "tick_type": [1, 1, 2, 1],
            "amount": [2_500_000, 3_060_000, 2_060_000, 9_801_000],
            "simtrade": [0, 0, 0, 0],
        }
        row = summarize_historical_ticks(
            ticks,
            ticker="2344",
            name="華邦電",
            market="上市",
            trade_date="2026-08-14",
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["large_buy_amount"], 5_560_000)
        self.assertEqual(row["large_sell_amount"], 2_060_000)
        self.assertEqual(row["late_large_buy_amount"], 3_060_000)
        self.assertEqual(row["total_turnover_amount"], 7_620_000)
        self.assertNotEqual(row["price_impact_pct"], 0)
        self.assertEqual(row["trade_date"], "2026-08-14")


if __name__ == "__main__":
    unittest.main()
