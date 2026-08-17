import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from main_force_collector import collect_once
from main_force_store import load_main_force_bars, main_force_storage_status, save_main_force_bars


class MainForceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_persists_multiple_days_without_zero_filling_missing_data(self):
        valid = {
            "ts": 1_786_400_400_000,
            "main_buy_volume": 12,
            "main_sell_volume": 7,
            "main_buy_amount": 1_200_000,
            "main_sell_amount": 700_000,
            "main_tick_count": 2,
            "main_force_available": True,
        }
        missing = {"ts": valid["ts"] + 60_000, "main_net_volume": 0}
        self.assertEqual(save_main_force_bars("2330", "1m", [valid, missing]), 1)
        rows = load_main_force_bars("2330", "1m", days=400)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["main_net_volume"], 5)
        self.assertEqual(main_force_storage_status()["stockCount"], 1)

    def test_collector_snapshots_all_active_codes_and_intervals(self):
        bar = {
            "ts": 1_786_400_400_000,
            "main_buy_volume": 3,
            "main_sell_volume": 1,
            "main_force_available": True,
        }

        class Service:
            def get_active_stock_codes(self):
                return ["2330", "2317"]

        class Hub:
            def get_live_bars_1m(self, code):
                return [bar]

            def get_live_bars(self, code):
                return [bar]

        result = collect_once(service=Service(), hub=Hub())
        self.assertEqual(result, {"stockCount": 2, "saved1m": 2, "saved5m": 2})
        self.assertEqual(main_force_storage_status()["barCount"], 4)


if __name__ == "__main__":
    unittest.main()
