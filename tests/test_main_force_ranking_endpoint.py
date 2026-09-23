"""persistent_app.py 的 /api/hub/main-force/ranking：只排 stock_groups 官方族群裡的股票。
主力副圖收集器會追蹤任何開過圖的股票（含 00632R／00991A 這類 ETF），使用者 2026-09-23
要求排行不要出現 ETF——ETF 不在 43 個主群（含股期標的清單）裡。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import persistent_app
from database import initialize_database
from disposition_prediction import official_group_code_names
from main_force_store import save_main_force_bars
from otc_index import taipei_trade_date

BASE_TS = 1_786_413_600_000  # 2026-08-11 10:00:00+08:00


class MainForceRankingEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.client = TestClient(persistent_app.app)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_ranking_excludes_codes_outside_official_groups(self):
        official = next(iter(official_group_code_names()))
        trade_date = taipei_trade_date(BASE_TS)
        for code, buy in ((official, 100), ("00632R", 900), ("00991A", 800)):
            save_main_force_bars(code, "5m", [{
                "ts": BASE_TS, "main_buy_volume": buy, "main_sell_volume": 0,
                "main_force_available": True,
            }])
        response = self.client.get("/api/hub/main-force/ranking", params={"trade_date": trade_date, "limit": 1000})
        self.assertEqual(response.status_code, 200)
        codes = [row["code"] for row in response.json()["ranking"]]
        self.assertEqual(codes, [official])
        self.assertEqual(response.json()["count"], 1)
