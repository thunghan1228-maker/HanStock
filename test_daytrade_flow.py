from __future__ import annotations

import unittest
from datetime import datetime

from daytrade_flow import (
    classify_daytrade_row,
    is_equity_code,
    latest_completed_trade_date,
    limit_up_price,
    summarize_historical_ticks,
)
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

    def test_limit_up_price_uses_taiwan_tick_size_and_rounds_down(self):
        self.assertEqual(limit_up_price(189), 207.5)
        self.assertEqual(limit_up_price(30.5), 33.55)
        self.assertEqual(limit_up_price(472), 519)

    def test_three_layers_prioritize_locked_then_touched(self):
        base = {
            "large_buy_amount": 5_000_000,
            "large_sell_amount": 1_000_000,
            "total_turnover_amount": 10_000_000,
            "late_large_buy_amount": 2_000_000,
            "day_change_pct": 9.8,
            "suspicion_score": 90,
        }
        self.assertEqual(classify_daytrade_row({**base, "closed_at_limit_up": True}), "漲停鎖定")
        self.assertEqual(classify_daytrade_row({**base, "reached_limit_up": True}), "曾達漲停")
        self.assertEqual(classify_daytrade_row(base), "強勢大單")

    def test_general_trading_is_not_listed(self):
        self.assertEqual(
            classify_daytrade_row(
                {
                    "large_buy_amount": 1_000_000,
                    "large_sell_amount": 2_000_000,
                    "total_turnover_amount": 10_000_000,
                    "day_change_pct": -1,
                    "suspicion_score": 15,
                }
            ),
            "",
        )

    def test_equity_universe_excludes_etfs_and_bonds(self):
        self.assertTrue(is_equity_code("1303"))
        self.assertTrue(is_equity_code("2887E"))
        self.assertTrue(is_equity_code("910322"))
        self.assertFalse(is_equity_code("0055"))
        self.assertFalse(is_equity_code("00679B"))
        self.assertFalse(is_equity_code("00919"))


if __name__ == "__main__":
    unittest.main()
