import os
import tempfile
import json
import unittest
from unittest.mock import MagicMock, patch

from finmind_active_etf_flow import active_etf_flow_for_ticker


class ActiveEtfFlowTest(unittest.TestCase):
    def test_aggregates_five_days_and_scores_selected_stock(self):
        responses = []
        for index in range(5):
            payload = {"data": [
                {"stock_id": "00980A", "component_stock_id": "2313", "buy": 12000 + index * 1000, "sell": 0},
                {"stock_id": "00981A", "component_stock_id": "2313", "buy": 0, "sell": 2000},
                {"stock_id": "00980A", "component_stock_id": "2330", "buy": 0, "sell": 50000},
            ]}
            response = MagicMock()
            response.read.return_value = json.dumps(payload).encode()
            response.__enter__.return_value = response
            responses.append(response)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"FINMIND_TOKEN": "test", "HANSTOCK_DATA_DIR": folder}), patch("finmind_active_etf_flow.urlopen", side_effect=responses):
            result = active_etf_flow_for_ticker("2313")
        self.assertEqual(result["tradingDays"], 5)
        self.assertEqual(result["latestNetShares"], 10000)
        self.assertEqual(result["fiveDayNetShares"], 60000)
        self.assertEqual(result["etfCount"], 2)
        self.assertGreater(result["score"], 0)


if __name__ == "__main__":
    unittest.main()
