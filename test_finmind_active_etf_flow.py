import os
import tempfile
import json
import unittest
from datetime import datetime as RealDatetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from finmind_active_etf_flow import active_etf_flow_for_ticker


class ActiveEtfFlowTest(unittest.TestCase):
    def test_aggregates_five_days_and_scores_selected_stock(self):
        responses = []
        for index in range(5):
            day = f"2026-08-{25 - index:02d}"
            payload = {"data": [
                {"date": day, "stock_id": "00980A", "component_stock_id": "2313", "buy": 12000 + index * 1000, "sell": 0},
                {"date": day, "stock_id": "00981A", "component_stock_id": "2313", "buy": 0, "sell": 2000},
                {"date": day, "stock_id": "00980A", "component_stock_id": "2330", "buy": 0, "sell": 50000},
            ]}
            response = MagicMock()
            response.read.return_value = json.dumps(payload).encode()
            response.__enter__.return_value = response
            responses.append(response)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"FINMIND_TOKEN": "test", "HANSTOCK_DATA_DIR": folder}), patch("finmind_active_etf_flow.urlopen", side_effect=responses), patch("finmind_active_etf_flow.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = RealDatetime(2026, 8, 25, tzinfo=ZoneInfo("Asia/Taipei"))
            result = active_etf_flow_for_ticker("2313")
        self.assertEqual(result["tradingDays"], 5)
        self.assertEqual(result["latestNetShares"], 10000)
        self.assertEqual(result["fiveDayNetShares"], 60000)
        self.assertEqual(result["etfCount"], 2)
        self.assertGreater(result["score"], 0)

    def test_filters_a_multi_day_response_and_does_not_cache_an_empty_day(self):
        payload = {"data": [
            {"date": "2026-08-25", "stock_id": "00980A", "component_stock_id": "2313", "buy": 12000, "sell": 0},
            {"date": "2026-08-26", "stock_id": "00980A", "component_stock_id": "2313", "buy": 5000, "sell": 0},
        ]}
        response = MagicMock()
        response.read.return_value = json.dumps(payload).encode()
        response.__enter__.return_value = response
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"FINMIND_TOKEN": "test", "HANSTOCK_DATA_DIR": folder}), patch("finmind_active_etf_flow.urlopen", return_value=response):
            from finmind_active_etf_flow import _fetch_day
            rows = _fetch_day("2026-08-25")
            empty = _fetch_day("2026-08-24")
            cache_root = Path(folder) / "finmind_active_etf"
            empty_cache_exists = (cache_root / "2026-08-24.json").exists()
        self.assertEqual([row["date"] for row in rows], ["2026-08-25"])
        self.assertEqual(empty, [])
        self.assertFalse(empty_cache_exists)

    def test_persistent_app_keeps_the_public_active_etf_route(self):
        source = (Path(__file__).parent / "persistent_app.py").read_text(encoding="utf-8")
        self.assertIn("from finmind_active_etf_flow import active_etf_flow_for_ticker", source)
        self.assertIn('@app.get("/api/hub/active-etf-flow")', source)


if __name__ == "__main__":
    unittest.main()
