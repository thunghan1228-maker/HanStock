"""disposition_phase3_store.py：存/讀當沖成交量跟借券賣出成交量的往返一致性、
ON CONFLICT更新不重複、依日期範圍查詢。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from database import initialize_database
from disposition_phase3_store import (
    load_day_trading_range,
    load_sbl_short_sale_range,
    save_day_trading_rows,
    save_sbl_short_sale_rows,
)


class DispositionPhase3StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_save_and_load_day_trading_round_trip(self):
        saved = save_day_trading_rows([{"code": "2330", "tradeDate": "2026-09-23", "volume": 1200.0}])
        self.assertEqual(saved, 1)
        rows = load_day_trading_range("2330", "2026-09-23", days=10)
        self.assertEqual(rows, [{"trade_date": "2026-09-23", "day_trading_volume": 1200.0}])

    def test_day_trading_upserts_on_conflict(self):
        save_day_trading_rows([{"code": "2330", "tradeDate": "2026-09-23", "volume": 1200.0}])
        save_day_trading_rows([{"code": "2330", "tradeDate": "2026-09-23", "volume": 1500.0}])
        rows = load_day_trading_range("2330", "2026-09-23", days=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["day_trading_volume"], 1500.0)

    def test_zero_volume_is_saved_not_treated_as_missing(self):
        """0是「有收集過但那天沒有當沖」的合法值，跟完全沒有row要能分辨。"""
        save_day_trading_rows([{"code": "2330", "tradeDate": "2026-09-23", "volume": 0.0}])
        rows = load_day_trading_range("2330", "2026-09-23", days=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["day_trading_volume"], 0.0)

    def test_save_and_load_sbl_short_sale_round_trip(self):
        saved = save_sbl_short_sale_rows([{"code": "2330", "tradeDate": "2026-09-23", "volume": 300.0}])
        self.assertEqual(saved, 1)
        rows = load_sbl_short_sale_range("2330", "2026-09-23", days=10)
        self.assertEqual(rows, [{"trade_date": "2026-09-23", "sbl_short_sale_volume": 300.0}])

    def test_range_ordered_oldest_to_newest_and_respects_end_date(self):
        for d in ("2026-09-19", "2026-09-20", "2026-09-21"):
            save_day_trading_rows([{"code": "2330", "tradeDate": d, "volume": 100.0}])
        rows = load_day_trading_range("2330", "2026-09-20", days=10)
        self.assertEqual([r["trade_date"] for r in rows], ["2026-09-19", "2026-09-20"])

    def test_range_limited_by_days(self):
        for d in ("2026-09-19", "2026-09-20", "2026-09-21"):
            save_sbl_short_sale_rows([{"code": "2330", "tradeDate": d, "volume": 50.0}])
        rows = load_sbl_short_sale_range("2330", "2026-09-21", days=2)
        self.assertEqual([r["trade_date"] for r in rows], ["2026-09-20", "2026-09-21"])

    def test_different_codes_do_not_mix(self):
        save_day_trading_rows([
            {"code": "2330", "tradeDate": "2026-09-23", "volume": 100.0},
            {"code": "2317", "tradeDate": "2026-09-23", "volume": 200.0},
        ])
        rows = load_day_trading_range("2330", "2026-09-23", days=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["day_trading_volume"], 100.0)


if __name__ == "__main__":
    unittest.main()
