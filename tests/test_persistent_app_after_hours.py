from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import persistent_app
from after_hours_fixed_price import save_after_hours_day

TW = timezone(timedelta(hours=8))


class AfterHoursFixedPriceEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        self.client = TestClient(persistent_app.app)
        self.today = datetime.now(TW).date()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _day(self, days_ago: int) -> str:
        return (self.today - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    def test_latest_falls_back_to_previous_collected_day_before_today_is_published(self) -> None:
        # 盤後定價分頁14:30前要直接顯示前一個交易日的14:30資料，不是空的。
        save_after_hours_day(self._day(3), [
            {"stock_code": "2330", "stock_name": "台積電", "price": 1105.0, "volume": 1000},
        ])
        save_after_hours_day(self._day(1), [
            {"stock_code": "2317", "stock_name": "鴻海", "price": 205.5, "volume": 5000},
        ])

        resp = self.client.get("/api/hub/after-hours-fixed-price", params={"latest": "true"})

        data = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["tradeDate"], self._day(1))
        self.assertFalse(data["isToday"])
        self.assertEqual([e["code"] for e in data["entries"]], ["2317"])

    def test_latest_prefers_today_once_collected(self) -> None:
        save_after_hours_day(self._day(1), [
            {"stock_code": "2317", "stock_name": "鴻海", "price": 205.5, "volume": 5000},
        ])
        save_after_hours_day(self._day(0), [
            {"stock_code": "2330", "stock_name": "台積電", "price": 1105.0, "volume": 1000},
        ])

        data = self.client.get("/api/hub/after-hours-fixed-price", params={"latest": "true"}).json()

        self.assertEqual(data["tradeDate"], self._day(0))
        self.assertTrue(data["isToday"])
        self.assertEqual([e["code"] for e in data["entries"]], ["2330"])

    def test_without_latest_keeps_returning_todays_possibly_empty_day(self) -> None:
        save_after_hours_day(self._day(1), [
            {"stock_code": "2317", "stock_name": "鴻海", "price": 205.5, "volume": 5000},
        ])

        data = self.client.get("/api/hub/after-hours-fixed-price").json()

        self.assertEqual(data["tradeDate"], self._day(0))
        self.assertTrue(data["isToday"])
        self.assertEqual(data["entries"], [])

    def test_explicit_trade_date_wins_over_latest(self) -> None:
        save_after_hours_day(self._day(3), [
            {"stock_code": "2330", "stock_name": "台積電", "price": 1105.0, "volume": 1000},
        ])
        save_after_hours_day(self._day(1), [
            {"stock_code": "2317", "stock_name": "鴻海", "price": 205.5, "volume": 5000},
        ])

        data = self.client.get(
            "/api/hub/after-hours-fixed-price",
            params={"latest": "true", "trade_date": self._day(3)},
        ).json()

        self.assertEqual(data["tradeDate"], self._day(3))
        self.assertEqual([e["code"] for e in data["entries"]], ["2330"])


if __name__ == "__main__":
    unittest.main()
