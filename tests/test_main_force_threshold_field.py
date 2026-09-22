"""主力副圖 API 要回真正的大戶門檻，前端才不會一直寫死「單筆成交量 ≥ 500 張（示範資料）」。"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import market_data_hub
import persistent_app


class MainForceThresholdFieldTests(unittest.TestCase):
    def test_force_bars_response_reports_the_real_lot_threshold(self) -> None:
        client = TestClient(persistent_app.app)
        resp = client.get("/api/hub/force/bars/2330?interval=5m&days=1&backfill=false")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["mainForceMinLots"], market_data_hub.MAIN_FORCE_MIN_LOTS)
        self.assertEqual(data["mainForceMinAmount"], market_data_hub.MAIN_FORCE_MIN_AMOUNT)
        self.assertGreaterEqual(data["mainForceMinLots"], 1)
        self.assertEqual(persistent_app.main_force_threshold(), {"minLots": market_data_hub.MAIN_FORCE_MIN_LOTS, "minAmount": market_data_hub.MAIN_FORCE_MIN_AMOUNT})


if __name__ == "__main__":
    unittest.main()
