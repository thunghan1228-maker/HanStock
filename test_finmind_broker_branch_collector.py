import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from finmind_broker_branch_collector import (
    BROKER_BRANCH_SOURCE,
    RollingHourlyLimiter,
    _latest_stock_universe,
    aggregate_branch_rows,
    broker_branch_collection_allowed,
    has_meaningful_day_coverage,
)


class FinMindBrokerBranchCollectorTests(unittest.TestCase):
    def test_pauses_full_market_finmind_backfill_during_taipei_market_hours(self):
        taipei = ZoneInfo("Asia/Taipei")
        self.assertFalse(broker_branch_collection_allowed(datetime(2026, 8, 26, 9, 0, tzinfo=taipei)))
        self.assertFalse(broker_branch_collection_allowed(datetime(2026, 8, 26, 13, 30, tzinfo=taipei)))
        self.assertTrue(broker_branch_collection_allowed(datetime(2026, 8, 26, 15, 10, tzinfo=taipei)))
        self.assertTrue(broker_branch_collection_allowed(datetime(2026, 8, 30, 10, 0, tzinfo=taipei)))

    def test_filters_current_twse_tpex_and_keeps_etf(self):
        rows = [
            {"stock_id": "0050", "stock_name": "元大台灣50", "industry_category": "ETF", "type": "twse", "date": "2026-08-21"},
            {"stock_id": "2330", "stock_name": "台積電", "industry_category": "半導體", "type": "twse", "date": "2026-08-21"},
            {"stock_id": "1294", "stock_name": "漢田生技", "industry_category": "生技", "type": "emerging", "date": "2024-09-25"},
            {"stock_id": "1294", "stock_name": "漢田生技", "industry_category": "生技", "type": "tpex", "date": "2026-08-21"},
            {"stock_id": "02001L", "stock_name": "測試ETN", "industry_category": "ETN", "type": "twse", "date": "2026-08-21"},
            {"stock_id": "084655", "stock_name": "測試權證", "industry_category": "權證", "type": "twse", "date": "2026-08-21"},
            {"stock_id": "1234", "stock_name": "已下市", "industry_category": "其他", "type": "twse", "date": "2020-01-01"},
        ]
        self.assertEqual(_latest_stock_universe(rows), ["0050", "1294", "2330"])

    def test_aggregates_net_amount_and_directional_top_five_concentration(self):
        rows = [
            {"securities_trader_id": "A", "price": 10, "buy": 100, "sell": 0},
            {"securities_trader_id": "B", "price": 10, "buy": 50, "sell": 0},
            {"securities_trader_id": "C", "price": 10, "buy": 0, "sell": 20},
        ]
        result = aggregate_branch_rows("2330", "2026-08-21", rows)
        self.assertEqual(result["netAmount"], 1300.0)
        self.assertEqual(result["activeBranches"], 3)
        self.assertEqual(result["concentration"], 100.0)
        self.assertEqual(result["source"], BROKER_BRANCH_SOURCE)

    def test_uses_top_five_imbalance_instead_of_all_branch_sum(self):
        rows = [
            {"securities_trader_id": "B1", "price": 10, "buy": 100, "sell": 0},
            {"securities_trader_id": "B2", "price": 10, "buy": 90, "sell": 0},
            {"securities_trader_id": "B3", "price": 10, "buy": 80, "sell": 0},
            {"securities_trader_id": "B4", "price": 10, "buy": 70, "sell": 0},
            {"securities_trader_id": "B5", "price": 10, "buy": 60, "sell": 0},
            {"securities_trader_id": "B6", "price": 10, "buy": 50, "sell": 0},
            {"securities_trader_id": "S1", "price": 10, "buy": 0, "sell": 75},
            {"securities_trader_id": "S2", "price": 10, "buy": 0, "sell": 75},
            {"securities_trader_id": "S3", "price": 10, "buy": 0, "sell": 75},
            {"securities_trader_id": "S4", "price": 10, "buy": 0, "sell": 75},
            {"securities_trader_id": "S5", "price": 10, "buy": 0, "sell": 75},
            {"securities_trader_id": "S6", "price": 10, "buy": 0, "sell": 75},
        ]
        result = aggregate_branch_rows("2330", "2026-08-21", rows)
        # 全部分點相加為 0，但買方前五大 4,000 元、賣方前五大
        # 3,750 元，因此主力分點淨額應為正 250 元。
        self.assertEqual(result["netAmount"], 250.0)
        self.assertAlmostEqual(result["concentration"], 400 / 450 * 100, places=4)

    def test_empty_day_is_saved_as_zero_not_missing(self):
        result = aggregate_branch_rows("2330", "2026-08-21", [])
        self.assertEqual(result["netAmount"], 0.0)
        self.assertEqual(result["concentration"], 0.0)
        self.assertEqual(result["activeBranches"], 0)

    def test_rejects_an_all_zero_market_day_but_accepts_majority_coverage(self):
        empty = [{"ticker": str(index), "activeBranches": 0} for index in range(10)]
        covered = [
            {"ticker": str(index), "activeBranches": 4 if index < 6 else 0}
            for index in range(10)
        ]
        self.assertFalse(has_meaningful_day_coverage(empty, 10))
        self.assertTrue(has_meaningful_day_coverage(covered, 10))

    def test_limiter_accepts_small_burst(self):
        limiter = RollingHourlyLimiter(limit=3, window_seconds=1)
        limiter.acquire()
        limiter.acquire()
        limiter.acquire()


if __name__ == "__main__":
    unittest.main()
