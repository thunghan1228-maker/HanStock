"""創高黑龍 12:00 暫定名單：即時K棒接在官方日K後面算整表、存取、套同一組參數、收盤整表進來就不給、排程時間窗、端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch
import brew_launch_history
import database
import grail_radar
import heilong_backtest
import heilong_noon as module
import persistent_app
from test_heilong_backtest import GROUPS, trading_dates

TW = module.TW_TZ


class NoonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.patches = [patch.object(m, "STOCK_GROUPS", GROUPS) for m in (heilong_backtest, brew_launch, brew_launch_history)]
        self.patches.append(patch.object(heilong_backtest, "HISTORY_DAYS", 60))
        self.patches.append(patch.object(heilong_backtest, "_warm_picker", lambda: None))
        for p in self.patches:
            p.start()
        brew_launch_history._group_by_code.clear()
        self.dates = trading_dates("2026-09-22", 241)     # 最後一天 9/22 是「今天」，官方日K只到 9/21
        d = self.dates
        rows = [("6182", x + "T00:00:00", 100, 101, 99, 100, 500) for x in d[:240]]
        rows += [("2330", x + "T00:00:00", 1000, 1001, 999, 1000, 20000) for x in d[:240]]
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        heilong_backtest.rebuild()
        self.live = {
            "6182": {"date": d[-1], "open": 104.0, "high": 105.0, "low": 101.0, "close": 102.0, "volume": 1000.0, "prevClose": 100.0},   # 創高收黑
            "2330": {"date": d[-1], "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1005.0, "volume": 9000.0, "prevClose": 1000.0},  # 紅K
        }
        self.universe = {"6182": {"name": "合晶", "market": "OTC"}, "2330": {"name": "台積電", "market": "TSE"}, "6488": {"name": "環球晶", "market": "OTC"}}

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        for p in self.patches:
            p.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def run_noon(self) -> dict:
        now = datetime.fromisoformat(self.dates[-1] + "T12:00:30+08:00")
        with patch.object(grail_radar, "_universe", return_value=self.universe), \
             patch.object(grail_radar, "fetch_live_bars", return_value=self.live):
            return module.run_noon(now=now)

    def test_build_and_section(self) -> None:
        d = self.dates
        rows = module.build_rows(d[-1], self.live)
        hx = rows["6182"]
        self.assertEqual((hx["score2"], hx["changePct"], hx["close"], hx["group"], hx["groupAvg2"], hx["inGroup"], hx["attention"]),
                         (15, 2.0, 102.0, "矽晶圓", 15.0, True, False))
        self.assertEqual(rows["2330"]["changePct"], 0.5)
        result = self.run_noon()
        self.assertEqual((result["date"], result["quotes"], result["rows"]), (d[-1], 2, 2))
        p = heilong_backtest.normalize_params({"algo": "official", "score": 10, "k": "black", "min": -10, "max": 3})
        sec = module.noon_section(p, d[-2])
        self.assertEqual((sec["date"], sec["at"], sec["count"]), (d[-1], sec["at"], 1))
        self.assertEqual((sec["rows"][0]["code"], sec["rows"][0]["k"], sec["rows"][0]["score2"]), ("6182", "black", 15))
        self.assertIsNone(module.noon_section(p, d[-1]))       # 收盤整表已經有今天：以正式名單為準
        # 創高黑龍整包也帶出來（收盤整表最新是昨天）
        payload = heilong_backtest.backtest({"algo": "official", "score": 10})
        self.assertEqual(payload["date"], d[-2])
        self.assertEqual([r["code"] for r in payload["noon"]["rows"]], ["6182"])

    def test_skip_when_quotes_missing(self) -> None:
        self.live = {}
        self.assertIn("skipped", self.run_noon())
        self.assertIsNone(module.load_noon())

    def test_due_window(self) -> None:
        at = lambda hm: datetime.fromisoformat(f"2026-09-22T{hm}:00+08:00")  # noqa: E731
        self.assertFalse(module.due(at("11:59"), None))
        self.assertTrue(module.due(at("12:00"), None))
        self.assertTrue(module.due(at("12:20"), "2026-09-21"))
        self.assertFalse(module.due(at("12:21"), None))
        self.assertFalse(module.due(at("12:05"), "2026-09-22"))                       # 今天算過了
        self.assertFalse(module.due(datetime.fromisoformat("2026-09-26T12:05:00+08:00"), None))   # 週六

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        with patch.object(module, "run_noon", return_value={"date": "2026-09-22", "rows": 2}):
            r = client.post("/api/hub/heilong/noon/run")
        self.assertEqual((r.status_code, r.json()["result"]["rows"]), (200, 2))


if __name__ == "__main__":
    unittest.main()
