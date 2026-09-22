"""驗證 hanstock_app.py 實際覆蓋/移除的正式路由行為。

正式服務是三層組裝而成：
  api_server.py（基礎路由，見 tests/test_api_realtime.py）
    → hanstock_app.py（移除、覆蓋掉其中幾個路由，這個檔案要驗證的就是這層）
      → persistent_app.py（疊加持久化端點；Railway 實際執行的就是這一層，見 run_api.py）

hanstock_app.py 把 api_server.py 原本的「/」（307 轉址到 /hub-dashboard）
和「/hub-dashboard」都拔掉，改成「/」直接回傳 web/index.html；也把
api_server.py 原本的 /api/hub/bars1m、/api/hub/bars 換成能跨 Railway
重啟復原歷史 K 棒的版本。這裡驗證的是換掉之後、使用者實際會打到的
行為，不是 api_server.py 自己（從未被部署過）的原始版本。
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import hanstock_app


class HanstockAppOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(hanstock_app.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def test_root_serves_bundled_homepage_instead_of_redirecting(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn("HanStock", response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store, max-age=0")

    def test_old_hub_dashboard_route_was_removed(self):
        response = self.client.get("/hub-dashboard")
        self.assertEqual(response.status_code, 404)

    def test_bars1m_route_uses_resilient_handler_not_the_removed_one(self):
        # api_server.py原本這個路由只回傳MarketDataHub當下的記憶體內容
        # (Railway重啟後會是空的)；hanstock_app.py換成get_resilient_stock_bars，
        # 會多帶一個"bootstrap"欄位。這裡只驗證換過來的是新版本，不驗證
        # 歷史回補的細節——那屬於stock_bar_bootstrap.py自己的測試範圍。
        response = self.client.get("/api/hub/bars1m/2330")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], "2330")
        self.assertIn("bootstrap", payload)


if __name__ == "__main__":
    unittest.main()
