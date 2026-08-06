from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class FakeState:
    logged_in = True


class FakeService:
    def __init__(self):
        self.state = FakeState()
        self.active = []

    def startup(self):
        return None

    def shutdown(self):
        return None

    def get_health(self):
        return {
            "shioaji_initialized": True,
            "shioaji_logged_in": True,
            "certificate_active": False,
            "quote_connected": True,
            "subscribed": True,
            "last_quote_time": "2026-08-04T09:00:00+08:00",
            "quote_age_seconds": 0.2,
            "quote_stale": False,
            "current_contract": "TXFR1",
            "last_event": "code=16",
            "data_source": "fake",
            "reconnect_count": 0,
            "error_message": None,
        }

    def get_stock_health(self):
        return {
            "enabled": True,
            "active_subscription_count": len(self.active),
            "subscription_limit": 150,
            "cached_quote_count": len(self.active),
            "active_codes": list(self.active),
        }

    def ensure_stock_subscriptions(self, codes):
        for code in codes:
            if code not in self.active:
                self.active.append(code)
        return {
            "requested": list(codes),
            "newly_subscribed": list(codes),
            "already_subscribed": [],
            "evicted": [],
            "failed": {},
            "active_count": len(self.active),
            "capacity": 150,
        }

    def get_stock_quotes(self, codes):
        return {
            code: {
                "code": code,
                "close": 100.0,
                "price_chg": 1.0,
                "pct_chg": 1.0,
                "total_volume": 1000,
                "quote_age_seconds": 0.1,
                "quote_stale": False,
                "subscribed": True,
            }
            for code in codes
        }

    def get_stock_quote(self, code):
        return self.get_stock_quotes([code])[code]

    def get_active_stock_codes(self):
        return list(self.active)

    def get_latest_tick(self):
        return {"code": "TXFH6", "close": 42000}


fake_service = FakeService()

config_module = types.ModuleType("config")
config_module.SHIOAJI_QUOTE_ENABLED = True
sys.modules["config"] = config_module

quote_module = types.ModuleType("quote_service")
quote_module.get_quote_service = lambda: fake_service
sys.modules["quote_service"] = quote_module

with tempfile.TemporaryDirectory() as temp_dir:
    result_path = Path(temp_dir) / "rule1.json"
    read_module = types.ModuleType("read_rule1_results")
    read_module.RESULT_PATH = result_path
    read_module.load_rule1_results = lambda: {
        "strategy": "Rule1",
        "generated_at": None,
        "summary": {},
        "groups": [],
    }
    sys.modules["read_rule1_results"] = read_module

    PATCH_DIR = Path(__file__).parents[1]
    sys.path.insert(0, str(PATCH_DIR))
    module_path = PATCH_DIR / "api_server.py"
    spec = importlib.util.spec_from_file_location("api_server_under_test", module_path)
    api_module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = api_module
    spec.loader.exec_module(api_module)


class RealtimeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(api_module.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def test_group_query_returns_ranked_quotes(self):
        response = self.client.get("/api/realtime/group/記憶體")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["groups"][0]["group_name"], "記憶體")
        self.assertGreater(payload["groups"][0]["available_quote_count"], 0)
        self.assertEqual(payload["groups"][0]["stocks"][0]["rank"], 1)

    def test_stock_code_resolves_full_group(self):
        response = self.client.get("/api/realtime/group/2344")
        self.assertEqual(response.status_code, 200)
        group_names = [group["group_name"] for group in response.json()["groups"]]
        self.assertIn("記憶體", group_names)

    def test_single_stock_endpoint(self):
        response = self.client.get("/api/realtime/2330")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data"]["stock_code"], "2330")
        self.assertTrue(payload["data"]["quote_available"])

    def test_hub_one_minute_bars_endpoint(self):
        hub = api_module.get_market_data_hub()
        hub.on_stock_tick({
            "code": "2330",
            "close": 100.0,
            "volume": 2,
            "tick_time": "2026-08-06T09:00:01+08:00",
        })
        hub.on_stock_tick({
            "code": "2330",
            "close": 101.0,
            "volume": 3,
            "tick_time": "2026-08-06T09:01:01+08:00",
        })
        response = self.client.get("/api/hub/bars1m/2330")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["interval"], "1m")
        self.assertEqual(payload["code"], "2330")
        self.assertEqual(payload["bar_count"], 2)

    def test_hub_one_minute_batch_endpoint_deduplicates(self):
        response = self.client.post(
            "/api/hub/bars1m/batch",
            json={"codes": ["2330", "2330", "2344"]},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["interval"], "1m")
        self.assertEqual(payload["requested_count"], 2)
        self.assertIn("2330", payload["data"])
        self.assertIn("2344", payload["data"])


if __name__ == "__main__":
    unittest.main()
