import unittest

from finmind_broker_branch_collector import (
    RollingHourlyLimiter,
    _latest_stock_universe,
    aggregate_branch_rows,
)


class FinMindBrokerBranchCollectorTests(unittest.TestCase):
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
        self.assertEqual(result["source"], "FinMind-derived/TWSE-TPEx")

    def test_empty_day_is_saved_as_zero_not_missing(self):
        result = aggregate_branch_rows("2330", "2026-08-21", [])
        self.assertEqual(result["netAmount"], 0.0)
        self.assertEqual(result["concentration"], 0.0)
        self.assertEqual(result["activeBranches"], 0)

    def test_limiter_accepts_small_burst(self):
        limiter = RollingHourlyLimiter(limit=3, window_seconds=1)
        limiter.acquire()
        limiter.acquire()
        limiter.acquire()


if __name__ == "__main__":
    unittest.main()
