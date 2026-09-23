"""disposition_fundamentals_store.py：存/讀基本面跟融資融券資料的往返一致性、
codes過濾、ON CONFLICT更新不重複。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from database import initialize_database
from disposition_fundamentals_store import (
    load_fundamentals_day,
    load_margin_short_day,
    load_margin_short_range,
    save_fundamentals_rows,
    save_margin_short_rows,
)


class DispositionFundamentalsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_save_and_load_fundamentals_round_trip(self):
        saved = save_fundamentals_rows([
            {"code": "2330", "tradeDate": "2026-09-23", "peRatio": 20.0, "pbr": 3.0, "dividendYield": 2.5, "marketValue": 1e13},
        ])
        self.assertEqual(saved, 1)
        loaded = load_fundamentals_day("2026-09-23")
        self.assertEqual(loaded["2330"]["peRatio"], 20.0)
        self.assertEqual(loaded["2330"]["marketValue"], 1e13)

    def test_save_upserts_on_conflict(self):
        save_fundamentals_rows([{"code": "2330", "tradeDate": "2026-09-23", "peRatio": 20.0}])
        save_fundamentals_rows([{"code": "2330", "tradeDate": "2026-09-23", "peRatio": 25.0}])
        loaded = load_fundamentals_day("2026-09-23")
        self.assertEqual(loaded["2330"]["peRatio"], 25.0)
        self.assertEqual(len(loaded), 1)

    def test_load_fundamentals_day_filters_by_codes(self):
        save_fundamentals_rows([
            {"code": "2330", "tradeDate": "2026-09-23", "peRatio": 20.0},
            {"code": "2317", "tradeDate": "2026-09-23", "peRatio": 10.0},
        ])
        loaded = load_fundamentals_day("2026-09-23", codes={"2330"})
        self.assertIn("2330", loaded)
        self.assertNotIn("2317", loaded)

    def test_save_and_load_margin_short_round_trip(self):
        save_margin_short_rows([
            {"code": "2330", "tradeDate": "2026-09-23", "marginTodayBalance": 800, "marginLimit": 2000, "shortTodayBalance": 200, "shortLimit": 500},
        ])
        loaded = load_margin_short_day("2026-09-23")
        self.assertEqual(loaded["2330"]["marginTodayBalance"], 800)
        self.assertEqual(loaded["2330"]["shortLimit"], 500)

    def test_load_margin_short_range_ordered_oldest_to_newest(self):
        for d in ("2026-09-19", "2026-09-20", "2026-09-21"):
            save_margin_short_rows([{"code": "2330", "tradeDate": d, "marginTodayBalance": 100, "shortTodayBalance": 10}])
        rows = load_margin_short_range("2330", "2026-09-21", days=10)
        self.assertEqual([r["trade_date"] for r in rows], ["2026-09-19", "2026-09-20", "2026-09-21"])

    def test_load_margin_short_range_respects_end_date(self):
        for d in ("2026-09-19", "2026-09-20", "2026-09-21"):
            save_margin_short_rows([{"code": "2330", "tradeDate": d, "marginTodayBalance": 100, "shortTodayBalance": 10}])
        rows = load_margin_short_range("2330", "2026-09-20", days=10)
        self.assertEqual([r["trade_date"] for r in rows], ["2026-09-19", "2026-09-20"])


if __name__ == "__main__":
    unittest.main()
