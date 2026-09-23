"""persistent_app.py的/api/hub/disposition-risk：priceExtremeWatch欄位涵蓋全部524檔
(不侷限在today已經觸發某款的股票，因為第十一款單日獨立判定、不算入第六條累積路徑)，
反推創6日新高/新低的明天收盤價門檻。"""

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


class DispositionRiskPriceExtremeWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.client = TestClient(persistent_app.app)
        # official_group_code_names()是從STOCK_GROUPS常數來的，不是DB資料，取其中
        # 真實存在的第一個代號，才會出現在524檔範圍裡。
        self.code = next(iter(official_group_code_names()))
        self.today = date(2026, 9, 23)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_bars(self, code: str, closes_by_date: dict[date, float]) -> None:
        with get_connection() as connection:
            rows = [
                (code, d.isoformat() + "T00:00:00+00:00", c, c, c, c, 1000.0)
                for d, c in closes_by_date.items()
            ]
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(stock_code, bar_time) DO UPDATE SET close = excluded.close",
                rows,
            )

    def test_qualifying_stock_appears_even_without_firing_anything_today(self):
        # 這檔今天沒有觸發任何款別(沒有clause_log)，第十一款差距預測仍然要出現，
        # 不像gapPrediction(第一款)侷限在clause_log.keys()裡。
        days = _business_days_ending(self.today, 6)
        closes = [1000.0, 1550.0, 1550.0, 1550.0, 1550.0, 1900.0]
        self._seed_bars(self.code, dict(zip(days, closes)))

        resp = self.client.get("/api/hub/disposition-risk", params={"trade_date": self.today.isoformat()})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("priceExtremeWatch", body)
        matches = [r for r in body["priceExtremeWatch"] if r["code"] == self.code]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["clause"], "十一")
        self.assertEqual(matches[0]["direction"], "up")
        self.assertAlmostEqual(matches[0]["thresholdClose"], 1900.0)
        # 這檔沒有firedToday/accumulation，不該出現在原本的results裡。
        self.assertEqual(body["results"], [])

    def test_no_candidates_gives_empty_list_not_error(self):
        resp = self.client.get("/api/hub/disposition-risk", params={"trade_date": self.today.isoformat()})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["priceExtremeWatch"], [])


if __name__ == "__main__":
    unittest.main()
