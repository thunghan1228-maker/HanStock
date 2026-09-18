import tempfile
import unittest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import database
import after_hours_fixed_price_collector as collector
from after_hours_fixed_price import load_after_hours_day, save_after_hours_day

TW_TZ = timezone(timedelta(hours=8))


class AfterHoursFixedPriceCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_skips_before_after_hours_session_starts(self):
        weekday_before_session = datetime(2026, 9, 18, 10, 0, tzinfo=TW_TZ)  # Friday
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = weekday_before_session
            result = collector.collect_once()
        self.assertEqual(result["skipped"], "before_after_hours_session")

    def test_skips_on_weekend(self):
        saturday = datetime(2026, 9, 19, 15, 0, tzinfo=TW_TZ)
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            result = collector.collect_once()
        self.assertEqual(result["skipped"], "weekend")

    def test_skips_when_already_collected_for_today(self):
        save_after_hours_day("2026-09-18", [
            {"stock_code": "2330", "stock_name": "台積電", "price": 1105.0, "volume": 1000},
        ])
        after_session = datetime(2026, 9, 18, 14, 40, tzinfo=TW_TZ)  # Friday
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = after_session
            result = collector.collect_once()
        self.assertEqual(result["skipped"], "already_collected")

    def test_fetches_and_saves_when_session_ready_and_not_collected(self):
        after_session = datetime(2026, 9, 18, 14, 40, tzinfo=TW_TZ)  # Friday
        entries = [{"stock_code": "2330", "stock_name": "台積電", "price": 1105.0, "volume": 1000}]
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = after_session
            with patch.object(collector, "fetch_after_hours_day", return_value=entries) as mock_fetch:
                result = collector.collect_once()
        mock_fetch.assert_called_once_with(date(2026, 9, 18))
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["saved"], 1)
        self.assertEqual(len(load_after_hours_day("2026-09-18")), 1)


if __name__ == "__main__":
    unittest.main()
