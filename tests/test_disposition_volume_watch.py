"""persistent_app.py的/api/hub/disposition-risk/volume-watch：trade_date預設要用
「目前有資料的最新一個交易日」，不是呼叫當下的日曆日期(今天盤中bars_1d還沒有今天
這筆，用今天當預設值永遠找不到任何資料)；比對用的「目前成交量」有市場數據中樞即時
資料就用(liveData=true)，沒有就退回門檻計算那天的收盤量(liveData=false)。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import persistent_app
from database import get_connection, initialize_database
from disposition_prediction import official_group_code_names


def _business_days_ending(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


class FakeHubBars:
    def __init__(self, latest: dict[str, dict]) -> None:
        self._latest = latest

    def get_all_latest(self) -> dict[str, dict]:
        return self._latest


class FakeHub:
    def __init__(self, latest: dict[str, dict]) -> None:
        self.bars = FakeHubBars(latest)


class DispositionVolumeWatchEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.client = TestClient(persistent_app.app)
        # official_group_code_names()是從STOCK_GROUPS常數來的，不是DB資料，沒辦法
        # 用測試資料造一個假代號進去讓它出現在524檔範圍裡，所以取其中真實存在的第一個。
        self.code = next(iter(official_group_code_names()))
        self.today = date(2026, 9, 23)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_volumes(self, code: str, volumes_by_date: dict[date, float], close: float = 100.0) -> None:
        with get_connection() as connection:
            rows = [
                (code, d.isoformat() + "T00:00:00+00:00", close, close, close, close, v)
                for d, v in volumes_by_date.items()
            ]
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(stock_code, bar_time) DO UPDATE SET volume = excluded.volume",
                rows,
            )

    def _seed_qualifying_clause_9(self) -> None:
        days = _business_days_ending(self.today, 60)
        volumes_by_date = {d: 100.0 for d in days[:-1]}
        volumes_by_date[days[-1]] = 1100.0
        self._seed_volumes(self.code, volumes_by_date)

    def test_falls_back_to_reference_volume_without_live_hub_data(self):
        self._seed_qualifying_clause_9()

        with patch("persistent_app.get_market_data_hub", return_value=FakeHub({})):
            resp = self.client.get(
                "/api/hub/disposition-risk/volume-watch", params={"trade_date": self.today.isoformat()},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["tradeDate"], self.today.isoformat())
        self.assertEqual(body["liveCount"], 0)
        matches = [r for r in body["results"] if r["code"] == self.code]
        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0]["liveData"])
        self.assertAlmostEqual(matches[0]["currentVolume"], 1100.0)

    def test_uses_live_hub_volume_when_available(self):
        self._seed_qualifying_clause_9()
        hub = FakeHub({self.code: {"total_volume": 3000}})

        with patch("persistent_app.get_market_data_hub", return_value=hub):
            resp = self.client.get(
                "/api/hub/disposition-risk/volume-watch", params={"trade_date": self.today.isoformat()},
            )

        body = resp.json()
        self.assertEqual(body["liveCount"], 1)
        matches = [r for r in body["results"] if r["code"] == self.code]
        self.assertTrue(matches[0]["liveData"])
        self.assertAlmostEqual(matches[0]["currentVolume"], 3000.0)
        # 量已經遠超過門檻(threshold約5*avg60)，detail要顯示已達門檻，不是還差多少。
        self.assertEqual(matches[0]["detail"], "已達觸發注意門檻")

    def test_defaults_trade_date_to_latest_available_not_caller_today(self):
        # 呼叫當下的日曆日期不見得跟這裡seed的資料一致(今天盤中bars_1d本來就不會有
        # 今天這筆)；預設值要用latest_daily_trade_date_before算出來的「最新有資料
        # 那天」，這裡驗證確實回傳seed的那個日期，不是別的。
        self._seed_qualifying_clause_9()

        with patch("persistent_app.get_market_data_hub", return_value=FakeHub({})):
            resp = self.client.get("/api/hub/disposition-risk/volume-watch")

        body = resp.json()
        self.assertEqual(body["tradeDate"], self.today.isoformat())

    def test_invalid_trade_date_returns_422(self):
        resp = self.client.get(
            "/api/hub/disposition-risk/volume-watch", params={"trade_date": "not-a-date"},
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
