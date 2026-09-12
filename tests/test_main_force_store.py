import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from main_force_collector import collect_once
from main_force_store import (
    load_main_force_bars,
    load_main_force_ranking,
    main_force_storage_status,
    save_main_force_bars,
)
from otc_index import taipei_trade_date


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

    def test_collector_only_rewrites_latest_two_bars(self):
        bars = [{
            "ts": 1_786_400_400_000 + offset * 60_000,
            "main_buy_volume": offset + 1,
            "main_sell_volume": 0,
            "main_force_available": True,
        } for offset in range(5)]

        class Service:
            def get_active_stock_codes(self):
                return ["2330"]

        class Hub:
            def get_live_bars_1m(self, code):
                return bars

            def get_live_bars(self, code):
                return bars

        result = collect_once(service=Service(), hub=Hub())
        self.assertEqual(result, {"stockCount": 1, "saved1m": 2, "saved5m": 2})
        self.assertEqual(len(load_main_force_bars("2330", "1m")), 2)

    def test_ranking_orders_by_absolute_net_volume_desc(self):
        base_ts = 1_786_400_400_000
        trade_date = taipei_trade_date(base_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 100, "main_sell_volume": 10,
            "main_force_available": True,
        }])
        save_main_force_bars("2317", "5m", [{
            "ts": base_ts, "main_buy_volume": 5, "main_sell_volume": 300,
            "main_force_available": True,
        }])
        save_main_force_bars("1101", "5m", [{
            "ts": base_ts, "main_buy_volume": 20, "main_sell_volume": 15,
            "main_force_available": True,
        }])

        ranking = load_main_force_ranking(trade_date, interval="5m", limit=2)

        self.assertEqual([row["code"] for row in ranking], ["2317", "2330"])
        self.assertEqual(ranking[0]["side"], "sell")
        self.assertEqual(ranking[0]["netVolume"], -295)
        self.assertEqual(ranking[0]["buyVolume"], 5)
        self.assertEqual(ranking[0]["sellVolume"], 300)
        self.assertEqual(ranking[1]["side"], "buy")
        self.assertEqual(ranking[1]["netVolume"], 90)

    def test_ranking_defaults_to_empty_when_no_data_for_date(self):
        self.assertEqual(load_main_force_ranking("2000-01-01"), [])


if __name__ == "__main__":
    unittest.main()
