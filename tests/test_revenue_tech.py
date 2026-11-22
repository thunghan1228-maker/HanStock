"""首頁「營收＋技術面」：營收分（年增、累計年增、加速、月增）＋技術分（均線分數換算），各 50 分。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch_history
import database
import persistent_app
import revenue_tech as module
from heilong_backtest import _schema as heilong_schema
from revenue_rank import _schema as revenue_schema


class RevenueTechTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.groups_patch = patch.object(brew_launch_history, "STOCK_GROUPS", {"PCB": [("2368", "金像電"), ("3037", "欣興")]})
        self.groups_patch.start()
        brew_launch_history._group_by_code.clear()
        module._cache.update({"at": 0.0, "rows": None, "meta": None})
        rev = [   # (代號, 月份, 名稱, 營收千元, 月增, 年增, 累計年增)
            ("2330", "2026-10", "台積電", 30_000_000, 5, 60, 35), ("2330", "2026-09", "台積電", 28_000_000, 1, 40, 30),
            ("2368", "2026-09", "金像電", 1_000_000, -3, 120, 55), ("2368", "2026-08", "金像電", 1_030_000, 2, 130, 50),   # 10 月還沒公布
            ("6182", "2026-10", "合晶", 500_000, 10, -5, 2), ("6182", "2026-09", "合晶", 450_000, 0, 10, 3),
            ("3037", "2026-10", "欣興", 2_000_000, 1, 200, 60), ("3037", "2026-09", "欣興", 1_900_000, 0, 150, 50),
            ("9999", "2026-10", "冷門", 10_000, 50, 300, 300),
        ]
        bars = [   # (日期, 代號, 收盤, 漲跌幅, 均線分數, 5 日均成交值億)
            ("2026-11-19", "2330", 1000, 1.0, 10, 300.0),
            ("2026-11-20", "2330", 1020, 2.0, 15, 320.0), ("2026-11-20", "2368", 110, -1.5, 12, 30.0),
            ("2026-11-20", "6182", 45, 3.0, 15, 5.0), ("2026-11-20", "3037", 200, 0.5, 3, 40.0),
            ("2026-11-20", "9999", 20, 9.9, 15, 0.3),                       # 5 日均成交值不到 5,000 萬
            ("2026-11-20", "1234", 50, 1.0, 15, 9.0),                       # 沒有營收資料
        ]
        with database.get_connection() as c:
            revenue_schema(c)
            heilong_schema(c)
            c.executemany("INSERT INTO revenue_month (code, ym, market, name, industry, rev, mom, yoy, cum_yoy) VALUES (?, ?, 'TSE', ?, '電子', ?, ?, ?, ?)", rev)
            c.executemany("INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, change_pct, score2, val5, hi_len) VALUES (?, ?, 1, 1, 1, ?, 1000, ?, ?, ?, 5)", bars)

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        self.groups_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_points(self) -> None:
        self.assertEqual(module.revenue_points(None, None, None, None), 0)
        self.assertEqual(module.revenue_points(0, 0, 0, None), 0)
        self.assertEqual(module.revenue_points(0.1, 0.1, 0.1, None), 2 + 2 + 5)
        self.assertEqual(module.revenue_points(100, 50, -1, 85), 20 + 15 + 10)
        self.assertEqual(module.revenue_points(30, 15, -1, 35), 12 + 8 + 3)
        self.assertEqual(module.revenue_points(-20, -5, 3, -50), 5)            # 年增還是負的：加速不給分
        self.assertEqual([module.tech_points(v) for v in (None, 0, 9, 12, 15)], [0, 0, 30, 40, 50])

    def test_ranks_by_combined_score(self) -> None:
        data = module.payload()
        self.assertEqual((data["status"], data["date"]), ("ok", "2026-11-20"))
        self.assertEqual(data["revMonths"], {"2026-10": 4, "2026-09": 1})
        self.assertEqual([r["code"] for r in data["rows"]], ["2330", "2368", "3037", "6182"])   # 冷門、沒營收的不列
        tsmc = data["rows"][0]
        self.assertEqual((tsmc["total"], tsmc["rev"], tsmc["tech"], tsmc["score2"], tsmc["revMonth"]), (93, 43, 50, 15, "2026-10"))
        self.assertEqual((tsmc["prevYoy"], tsmc["revYi"], tsmc["chg"]), (40, 300.0, 2.0))
        gold = data["rows"][1]
        self.assertEqual((gold["total"], gold["rev"], gold["revMonth"], gold["group"], gold["prevYoy"]), (75, 35, "2026-09", "PCB", 130))   # 10 月還沒公布用 9 月
        self.assertEqual([r["total"] for r in data["rows"][2:]], [60, 57])
        self.assertEqual(len(module.payload(top=2)["rows"]), 2)

    def test_endpoint_and_empty(self) -> None:
        client = TestClient(persistent_app.app)
        body = client.get("/api/hub/revenue-tech?top=5").json()
        self.assertEqual((body["status"], body["rows"][0]["code"]), ("ok", "2330"))
        self.assertEqual(client.get("/api/hub/revenue-tech?top=1").status_code, 422)
        with database.get_connection() as c:
            c.execute("DELETE FROM revenue_month")
        module._cache.update({"rows": None})
        self.assertEqual(module.payload()["status"], "empty")


if __name__ == "__main__":
    unittest.main()
