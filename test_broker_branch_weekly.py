import sqlite3
import tempfile
import unittest
from pathlib import Path

import broker_branch_weekly as target


class BrokerBranchWeeklyTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "test.db"

        def connection():
            db = sqlite3.connect(self.database_path)
            db.row_factory = sqlite3.Row
            return db

        self.original_connection = target.get_connection
        target.get_connection = connection

    def tearDown(self):
        target.get_connection = self.original_connection
        self.tempdir.cleanup()

    def test_requires_five_complete_trade_dates(self):
        rows = []
        for index, day in enumerate(["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]):
            rows.append({"ticker": "2330", "tradeDate": day, "netAmount": 100 + index, "concentration": 12, "activeBranches": 4})
        rows.append({"ticker": "2317", "tradeDate": "2026-08-21", "netAmount": -50, "concentration": 8, "activeBranches": 3})
        target.save_broker_branch_daily(target.normalize_daily_rows(rows))
        result = target.read_latest_broker_branch_weekly()
        self.assertTrue(result["complete"])
        self.assertEqual(result["weekEndDate"], "2026/08/21")
        self.assertEqual([row["ticker"] for row in result["rows"]], ["2330"])
        self.assertEqual(result["rows"][0]["netAmount"], 510.0)

    def test_does_not_publish_an_incomplete_week(self):
        rows = [{"ticker": "2330", "tradeDate": "2026-08-21", "netAmount": 100, "concentration": 12, "activeBranches": 4}]
        target.save_broker_branch_daily(target.normalize_daily_rows(rows))
        result = target.read_latest_broker_branch_weekly()
        self.assertFalse(result["complete"])
        self.assertEqual(result["rows"], [])


if __name__ == "__main__":
    unittest.main()
