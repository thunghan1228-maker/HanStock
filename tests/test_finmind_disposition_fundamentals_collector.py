"""finmind_disposition_fundamentals_collector.py：沒有token直接跳過(不假裝有資料)、
_latest_row挑對日期的那筆、collect_trade_date把三個資料集併起來存、單一股票失敗不
拖垮整批。全部mock掉實際的FinMind fetch函式，不打真的網路。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from database import initialize_database
import finmind_disposition_fundamentals_collector as collector
from disposition_fundamentals_store import load_fundamentals_day, load_margin_short_day


class LatestRowTests(unittest.TestCase):
    def test_picks_row_matching_exact_date(self):
        rows = [{"date": "2026-09-22", "PER": 1.0}, {"date": "2026-09-23", "PER": 2.0}]
        self.assertEqual(collector._latest_row(rows, "2026-09-23")["PER"], 2.0)

    def test_falls_back_to_last_row_when_no_exact_match(self):
        rows = [{"date": "2026-09-20", "PER": 1.0}]
        self.assertEqual(collector._latest_row(rows, "2026-09-23")["PER"], 1.0)

    def test_empty_rows_returns_none(self):
        self.assertIsNone(collector._latest_row([], "2026-09-23"))


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

    def test_saves_fundamentals_and_margin_short_for_each_code(self):
        with (
            patch.object(collector, "fetch_per_pbr", return_value={"peRatio": 20.0, "pbr": 3.0, "dividendYield": 1.0}),
            patch.object(collector, "fetch_market_value", return_value=1e13),
            patch.object(collector, "fetch_margin_short", return_value={
                "marginTodayBalance": 800, "marginLimit": 2000, "shortTodayBalance": 200, "shortLimit": 500,
            }),
        ):
            result = collector.collect_trade_date("2026-09-23", ["2330"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["savedFundamentals"], 1)
        self.assertEqual(result["savedMarginShort"], 1)
        fundamentals = load_fundamentals_day("2026-09-23")
        self.assertEqual(fundamentals["2330"]["peRatio"], 20.0)
        margin = load_margin_short_day("2026-09-23")
        self.assertEqual(margin["2330"]["marginLimit"], 2000)

    def test_one_stock_failure_does_not_block_others(self):
        def fake_per_pbr(code, trade_date):
            if code == "BAD":
                raise RuntimeError("boom")
            return {"peRatio": 10.0, "pbr": 1.0, "dividendYield": None}

        with (
            patch.object(collector, "fetch_per_pbr", side_effect=fake_per_pbr),
            patch.object(collector, "fetch_market_value", return_value=None),
            patch.object(collector, "fetch_margin_short", return_value=None),
        ):
            result = collector.collect_trade_date("2026-09-23", ["BAD", "2330"])
        self.assertEqual(result["status"], "ok")
        fundamentals = load_fundamentals_day("2026-09-23")
        self.assertIn("2330", fundamentals)
        self.assertNotIn("BAD", fundamentals)

    def test_missing_market_value_still_saves_pe_pbr(self):
        with (
            patch.object(collector, "fetch_per_pbr", return_value={"peRatio": 10.0, "pbr": 1.0, "dividendYield": None}),
            patch.object(collector, "fetch_market_value", return_value=None),
            patch.object(collector, "fetch_margin_short", return_value=None),
        ):
            collector.collect_trade_date("2026-09-23", ["2330"])
        fundamentals = load_fundamentals_day("2026-09-23")
        self.assertEqual(fundamentals["2330"]["peRatio"], 10.0)
        self.assertIsNone(fundamentals["2330"]["marketValue"])


if __name__ == "__main__":
    unittest.main()
