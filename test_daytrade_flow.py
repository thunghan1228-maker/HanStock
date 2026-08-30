from __future__ import annotations

import unittest
from datetime import datetime

from daytrade_flow import (
    classify_daytrade_row,
    is_equity_code,
    latest_completed_trade_date,
    limit_up_price,
    missing_main_force_row,
    summarize_persisted_main_force_bars,
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
        self.assertTrue(row["main_force_data_available"])
        self.assertEqual(row["main_force_data_status"], "historical_ticks")

    def test_empty_historical_ticks_are_not_presented_as_real_zeroes(self):
        row = summarize_historical_ticks(
            {"ts": [], "close": [], "volume": []},
            ticker="3037",
            name="欣興",
            market="上市",
            trade_date="2026-08-17",
            daily_meta={"reached_limit_up": True, "official_turnover_amount": 4_000_000_000},
        )
        self.assertIsNone(row)

    def test_persisted_intraday_main_force_bars_are_used_as_fallback(self):
        bars = [
            {"trade_date": "2026-08-17", "ts": int(datetime(2026, 8, 17, 9, 1, tzinfo=TW_TZ).timestamp() * 1000), "main_buy_amount": 80_000_000, "main_sell_amount": 20_000_000, "main_force_available": True},
            {"trade_date": "2026-08-17", "ts": int(datetime(2026, 8, 17, 13, 5, tzinfo=TW_TZ).timestamp() * 1000), "main_buy_amount": 30_000_000, "main_sell_amount": 10_000_000, "main_force_available": True},
        ]
        row = summarize_persisted_main_force_bars(
            bars,
            ticker="3037",
            name="欣興",
            market="上市",
            trade_date="2026-08-17",
            daily_meta={
                "open_price": 100, "close_price": 110, "reference_price": 100,
                "limit_up_price": 110, "day_change_pct": 10,
                "official_turnover_amount": 4_000_000_000,
                "reached_limit_up": True, "closed_at_limit_up": True,
            },
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["large_buy_amount"], 110_000_000)
        self.assertEqual(row["large_sell_amount"], 30_000_000)
        self.assertEqual(row["late_large_buy_amount"], 30_000_000)
        self.assertTrue(row["main_force_data_available"])
        self.assertEqual(row["main_force_data_status"], "persisted_intraday_bars")

    def test_missing_source_is_explicitly_marked_pending_backfill(self):
        row = missing_main_force_row(
            ticker="3037",
            name="欣興",
            market="上市",
            trade_date="2026-08-17",
            daily_meta={
                "open_price": 100, "close_price": 110, "reference_price": 100,
                "limit_up_price": 110, "day_change_pct": 10,
                "official_turnover_amount": 4_000_000_000,
                "reached_limit_up": True, "closed_at_limit_up": True,
            },
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertFalse(row["main_force_data_available"])
        self.assertEqual(row["main_force_data_status"], "pending_backfill")

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

    def test_explicit_group_query_can_keep_unclassified_stock_data(self):
        ticks = {
            "ts": [datetime(2026, 8, 28, 9, 5, 0, tzinfo=TW_TZ)],
            "close": [100],
            "volume": [20],
            "tick_type": [2],
            "amount": [2_000_000],
            "simtrade": [0],
        }
        self.assertIsNone(summarize_historical_ticks(
            ticks,
            ticker="3317",
            name="尼克森",
            market="上櫃",
            trade_date="2026-08-28",
            daily_meta={"official_turnover_amount": 50_000_000, "day_change_pct": 0},
        ))
        row = summarize_historical_ticks(
            ticks,
            ticker="3317",
            name="尼克森",
            market="上櫃",
            trade_date="2026-08-28",
            daily_meta={"official_turnover_amount": 50_000_000, "day_change_pct": 0},
            include_unclassified=True,
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["category"], "")
        self.assertEqual(row["large_buy_amount"] - row["large_sell_amount"], -2_000_000)

    def test_equity_universe_excludes_etfs_and_bonds(self):
        self.assertTrue(is_equity_code("1303"))
        self.assertTrue(is_equity_code("2887E"))
        self.assertTrue(is_equity_code("910322"))
        self.assertFalse(is_equity_code("0055"))
        self.assertFalse(is_equity_code("00679B"))
        self.assertFalse(is_equity_code("00919"))


if __name__ == "__main__":
    unittest.main()
