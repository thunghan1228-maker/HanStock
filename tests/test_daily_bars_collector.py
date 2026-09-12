import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from database import initialize_database, save_bars
from datetime import datetime, timezone

import daily_bars_collector as collector

UTC = timezone.utc


class DailyBarsCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_first_run_does_a_full_backfill(self):
        with patch.object(collector, "download_official_daily_bars", return_value={"inserted_bars": 0}) as mock_download:
            result = collector.collect_once()
        mock_download.assert_called_once_with(days=collector.BACKFILL_DAYS, run_triangle_scan=False)
        self.assertEqual(result["mode"], "backfill")

    def test_subsequent_runs_only_catch_up_recent_days(self):
        save_bars("bars_1d", "2330", [{
            "time": datetime(2026, 9, 10, tzinfo=UTC),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000,
        }])
        with patch.object(collector, "download_official_daily_bars", return_value={"inserted_bars": 0}) as mock_download:
            result = collector.collect_once()
        mock_download.assert_called_once_with(days=collector.CATCHUP_DAYS, run_triangle_scan=False)
        self.assertEqual(result["mode"], "catchup")

    def test_collect_once_prunes_after_downloading(self):
        for day in range(1, 40):
            save_bars("bars_1d", "2330", [{
                "time": datetime(2026, 8 if day <= 31 else 9, day if day <= 31 else day - 31, tzinfo=UTC),
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000,
            }])
        with patch.object(collector, "download_official_daily_bars", return_value={"inserted_bars": 0}):
            with patch.object(collector, "KEEP_DAYS", 5):
                result = collector.collect_once()
        self.assertGreater(result["pruned"], 0)


if __name__ == "__main__":
    unittest.main()
