"""驗證 api_server.py 自己的路由行為（基礎層，從未被部署）。

正式服務是三層組裝：api_server.py → hanstock_app.py(移除/覆蓋部分路由，
見tests/test_hanstock_app_overrides.py) → persistent_app.py(疊加持久化
端點；Railway實際執行的是這一層，見run_api.py)。這個檔案只測試最底層
api_server.py自己的原始行為，其中「/」與「/hub-dashboard」在正式環境
會被hanstock_app.py換掉，不是使用者實際會看到的版本。
"""

from __future__ import annotations

import importlib.util
import asyncio
import os
import sys
import tempfile
import types
import unittest
import anyio
import httpx
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

# api_server.py的CORS middleware在模組載入當下就讀取這個環境變數；正式
# 環境由Railway設定成包含真正的前端網域。這裡先設好，讓gzip/CORS測試
# 不必依賴外部環境設定就能自我驗證。
os.environ.setdefault("HANSTOCK_CORS_ORIGINS", "https://www.hanstock.xyz,http://localhost:3000")


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

api_module = None

# sys.modules是同一個pytest process裡所有測試檔案共用的全域狀態。這裡
# 插入的config/quote_service/read_rule1_results假模組如果沒有在這個檔案
# 的測試跑完後還原，會讓後面才import quote_service的測試檔案(例如
# tests/test_hanstock_app_overrides.py)拿到不完整的假模組，出現看起來
# 毫不相干、其實只是缺了某個屬性的錯誤——這正是main_force_backfill那次
# 汙染的同一種問題，所以這裡不逐一補missing attribute，而是把整個注入
# 過程收斂進setUpModule/tearDownModule，用完就還原。
_PATCHED_MODULE_KEYS = ("config", "quote_service", "read_rule1_results", "api_server_under_test")
_saved_sys_modules: dict[str, object] = {}


def setUpModule():
    global api_module
    for key in _PATCHED_MODULE_KEYS:
        if key in sys.modules:
            _saved_sys_modules[key] = sys.modules[key]

    # api_server.py的CORS middleware在模組載入當下就讀取這個環境變數；
    # 正式環境由Railway設定成包含真正的前端網域，這裡先設好預設值，讓
    # gzip/CORS測試不必依賴外部環境設定就能自我驗證。
    os.environ.setdefault("HANSTOCK_CORS_ORIGINS", "https://www.hanstock.xyz,http://localhost:3000")

    config_module = types.ModuleType("config")
    config_module.SHIOAJI_QUOTE_ENABLED = True
    sys.modules["config"] = config_module

    quote_module = types.ModuleType("quote_service")
    quote_module.get_quote_service = lambda: fake_service
    quote_module.quote_deployment_role = lambda: "primary"
    quote_module.quote_startup_delay_seconds = lambda: 0.0
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

        patch_dir = Path(__file__).parents[1]
        sys.path.insert(0, str(patch_dir))
        module_path = patch_dir / "api_server.py"
        spec = importlib.util.spec_from_file_location("api_server_under_test", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        api_module = module


def tearDownModule():
    for key in _PATCHED_MODULE_KEYS:
        if key in _saved_sys_modules:
            sys.modules[key] = _saved_sys_modules[key]
        else:
            sys.modules.pop(key, None)


class RealtimeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(api_module.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def test_root_redirects_to_hub_dashboard(self):
        # 正式環境這個路由被hanstock_app.py換掉(見tests/test_hanstock_app_
        # overrides.py)，這裡驗證的只是api_server.py自己原本、從未部署過
        # 的版本。
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/hub-dashboard")

    def test_hub_dashboard_remains_available(self):
        # 同上：正式環境這個路由已經被hanstock_app.py整個移除(404)，
        # 這裡驗證的只是api_server.py自己這一層還定義著它。
        response = self.client.get("/hub-dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("HanStock", response.text)

    def test_large_market_response_gzip_preserves_payload_and_cors(self):
        path = "/api/realtime/group/記憶體"
        plain = self.client.get(path, headers={"Accept-Encoding": "identity"})
        compressed = self.client.get(path, headers={"Accept-Encoding": "gzip", "Origin": "https://www.hanstock.xyz"})
        self.assertEqual(compressed.json(), plain.json())
        self.assertEqual(compressed.headers.get("Content-Encoding"), "gzip")
        self.assertIn("Accept-Encoding", compressed.headers.get("Vary", ""))
        self.assertLess(int(compressed.headers["Content-Length"]), len(plain.content))
        self.assertIn("access-control-allow-origin", compressed.headers)
        self.assertNotIn("Content-Encoding", plain.headers)

    def test_small_redirect_is_not_compressed(self):
        response = self.client.get("/", headers={"Accept-Encoding": "gzip"}, follow_redirects=False)
        self.assertNotIn("Content-Encoding", response.headers)

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


class RealtimeApiSaturationTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_reads_bypass_exhausted_history_worker_pool(self):
        # Occupy every synchronous request token, as hanging SDK queries do.
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous_limit = limiter.total_tokens
        limiter.total_tokens = 1
        owner = object()
        await limiter.acquire_on_behalf_of(owner)
        from market_data_hub import MarketDataHub
        hub = MarketDataHub()
        hub_patch = patch.object(api_module, "get_market_data_hub", return_value=hub)
        hub_patch.start()
        hub.on_stock_tick({
            "code": "2330", "close": 102.0, "volume": 2,
            "tick_time": "2026-08-06T09:02:01+08:00",
        })
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api_module.app), base_url="http://test",
            ) as client:
                responses = await asyncio.wait_for(asyncio.gather(
                    client.get("/api/health"),
                    client.get("/api/hub/ticks?codes=2330"),
                    client.post("/api/hub/bars1m/batch", json={"codes": ["2330"]}),
                    client.post("/api/hub/bars/batch", json={"codes": ["2330"]}),
                ), timeout=1)
                self.assertEqual([r.status_code for r in responses], [200] * 4)
                self.assertEqual(responses[0].json()["api_status"], "ok")
                self.assertEqual(responses[1].json()["data"]["2330"]["close"], 102.0)
                for response in responses[2:]:
                    self.assertEqual(response.json()["data"]["2330"][-1]["close"], 102.0)
        finally:
            hub_patch.stop()
            limiter.release_on_behalf_of(owner)
            limiter.total_tokens = previous_limit


if __name__ == "__main__":
    unittest.main()
