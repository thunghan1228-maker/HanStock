"""finmind_disposition_phase3_collector.py：沒有token直接跳過、全市場單日查詢正確
解析Volume/SBLShortSalesShortSales欄位、requested codes裡FinMind沒回傳的存0(不是
略過)、單一資料集抓取失敗不拖垮另一個資料集。全部mock掉_request_json，不打真的網路。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from database import initialize_database
import finmind_disposition_phase3_collector as collector
from disposition_phase3_store import load_day_trading_range, load_sbl_short_sale_range


class FetchByCodeTests(unittest.TestCase):
    def test_fetch_day_trading_parses_volume_field(self):
        rows = [
            {"stock_id": "2330", "date": "2026-09-23", "Volume": 1200},
            {"stock_id": "2317", "date": "2026-09-23", "Volume": 300},
        ]
        with patch.object(collector, "_request_json", return_value=rows):
            result = collector.fetch_day_trading_volume_by_code("2026-09-23")
        self.assertEqual(result, {"2330": 1200.0, "2317": 300.0})

    def test_fetch_sbl_parses_short_sales_field_only(self):
        rows = [{"stock_id": "2330", "SBLShortSalesShortSales": 80, "SBLShortSalesCurrentDayBalance": 999}]
        with patch.object(collector, "_request_json", return_value=rows):
            result = collector.fetch_sbl_short_sale_volume_by_code("2026-09-23")
        self.assertEqual(result, {"2330": 80.0})

    def test_missing_value_field_skips_row(self):
        rows = [{"stock_id": "2330"}]
        with patch.object(collector, "_request_json", return_value=rows):
            result = collector.fetch_day_trading_volume_by_code("2026-09-23")
        self.assertEqual(result, {})


class CollectTradeDateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.token_patch = patch.object(collector, "_token", return_value="fake-token")
        self.token_patch.start()

    def tearDown(self):
        self.token_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_skips_without_token(self):
        with patch.object(collector, "_token", return_value=""):
            result = collector.collect_trade_date("2026-09-23", ["2330"])
        self.assertEqual(result["status"], "skipped")

    def test_saves_both_datasets_for_requested_codes(self):
        with (
            patch.object(collector, "fetch_day_trading_volume_by_code", return_value={"2330": 1200.0}),
            patch.object(collector, "fetch_sbl_short_sale_volume_by_code", return_value={"2330": 80.0}),
        ):
            result = collector.collect_trade_date("2026-09-23", ["2330"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["savedDayTrading"], 1)
        self.assertEqual(result["savedSblShortSale"], 1)
        day_trading = load_day_trading_range("2330", "2026-09-23", days=1)
        self.assertEqual(day_trading[0]["day_trading_volume"], 1200.0)
        sbl = load_sbl_short_sale_range("2330", "2026-09-23", days=1)
        self.assertEqual(sbl[0]["sbl_short_sale_volume"], 80.0)

    def test_code_absent_from_finmind_response_is_saved_as_zero(self):
        with (
            patch.object(collector, "fetch_day_trading_volume_by_code", return_value={}),
            patch.object(collector, "fetch_sbl_short_sale_volume_by_code", return_value={}),
        ):
            collector.collect_trade_date("2026-09-23", ["2330"])
        day_trading = load_day_trading_range("2330", "2026-09-23", days=1)
        self.assertEqual(day_trading[0]["day_trading_volume"], 0.0)

    def test_day_trading_failure_does_not_block_sbl(self):
        with (
            patch.object(collector, "fetch_day_trading_volume_by_code", side_effect=RuntimeError("boom")),
            patch.object(collector, "fetch_sbl_short_sale_volume_by_code", return_value={"2330": 80.0}),
        ):
            result = collector.collect_trade_date("2026-09-23", ["2330"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["savedDayTrading"], 0)
        self.assertEqual(result["savedSblShortSale"], 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertIn("TaiwanStockDayTrading", result["failed"][0])
        # 失敗的資料集當天完全不存row，不是存0——跟"有收集過但是0"要能分辨。
        self.assertEqual(load_day_trading_range("2330", "2026-09-23", days=1), [])

    def test_sbl_failure_does_not_block_day_trading(self):
        with (
            patch.object(collector, "fetch_day_trading_volume_by_code", return_value={"2330": 1200.0}),
            patch.object(collector, "fetch_sbl_short_sale_volume_by_code", side_effect=RuntimeError("boom")),
        ):
            result = collector.collect_trade_date("2026-09-23", ["2330"])
        self.assertEqual(result["savedDayTrading"], 1)
        self.assertEqual(result["savedSblShortSale"], 0)
        self.assertEqual(load_sbl_short_sale_range("2330", "2026-09-23", days=1), [])


if __name__ == "__main__":
    unittest.main()
