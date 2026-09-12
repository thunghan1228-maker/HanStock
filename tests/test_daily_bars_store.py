import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import database
from database import get_connection, initialize_database, save_bars
from daily_bars_store import (
    daily_bars_storage_status,
    load_daily_bars,
    prune_old_daily_bars,
)

UTC = timezone.utc


class DailyBarsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_day(self, code, day, close=100.0):
        save_bars("bars_1d", code, [{
            "time": datetime(2026, 9, day, tzinfo=UTC),
            "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000,
        }])

    def test_load_daily_bars_returns_oldest_to_newest(self):
        self._seed_day("2330", 10, close=100.0)
        self._seed_day("2330", 11, close=101.0)
        self._seed_day("2330", 12, close=102.0)
        bars = load_daily_bars("2330")
        self.assertEqual([b["close"] for b in bars], [100.0, 101.0, 102.0])
        self.assertTrue(bars[0]["ts"].startswith("2026-09-10"))

    def test_load_daily_bars_ignores_other_stocks(self):
        self._seed_day("2330", 10)
        self._seed_day("2317", 10)
        self.assertEqual(len(load_daily_bars("2330")), 1)

    def test_prune_old_daily_bars_keeps_only_recent_trading_days(self):
        for day in range(10, 16):  # 6 個交易日
            self._seed_day("2330", day)
        deleted = prune_old_daily_bars(keep_days=4)
        remaining_dates = sorted({b["ts"][:10] for b in load_daily_bars("2330", limit=100)})
        self.assertEqual(remaining_dates, ["2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15"])
        self.assertEqual(deleted, 2)

    def test_prune_old_daily_bars_no_op_when_fewer_days_than_keep(self):
        self._seed_day("2330", 10)
        self.assertEqual(prune_old_daily_bars(keep_days=4), 0)

    def test_storage_status_reports_counts_and_date_range(self):
        self._seed_day("2330", 10)
        self._seed_day("2330", 11)
        self._seed_day("2317", 11)
        status = daily_bars_storage_status()
        self.assertEqual(status["barCount"], 3)
        self.assertEqual(status["stockCount"], 2)
        self.assertEqual(status["firstTradeDate"], "2026-09-10")
        self.assertEqual(status["lastTradeDate"], "2026-09-11")


if __name__ == "__main__":
    unittest.main()
