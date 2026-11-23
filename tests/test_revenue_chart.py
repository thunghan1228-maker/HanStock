"""個股月營收圖：FinMind 月營收＋月底收盤（快取 6 小時）、鏡像較新的月份補上、單季三率、基本面／技術面分數。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch_history
import database
import financials
import persistent_app
import revenue_chart as module
from heilong_backtest import _schema as heilong_schema
from revenue_rank import _schema as revenue_schema

TW = timezone(timedelta(hours=8))
NOW = datetime(2026, 12, 5, 10, 0, tzinfo=TW)


def ym_list(start: str, n: int) -> list[str]:
    return [module._ym_add(start, i) for i in range(n)]


def fake_finmind(calls: list):
    def fetch(dataset: str, code: str, start: str):
        calls.append((dataset, code, start))
        if dataset == "TaiwanStockMonthRevenue":
            months = ym_list("2022-11", 48)            # 2022-11 ～ 2026-10
            return {"data": [{"revenue_year": int(m[:4]), "revenue_month": int(m[5:]), "revenue": 100e8 * (1 + 0.01 * i)} for i, m in enumerate(months)]}
        rows = []
        for i, m in enumerate(ym_list("2023-11", 38)):
            rows.append({"date": m + "-10", "close": 900.0})
            rows.append({"date": m + "-28", "close": 1000.0 + i})   # 月底那天
        return {"data": rows}
    return fetch


class RevenueChartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.groups_patch = patch.object(brew_launch_history, "STOCK_GROUPS", {"半導體": [("2330", "台積電")]})
        self.groups_patch.start()
        brew_launch_history._group_by_code.clear()
        module._scores.update({"at": 0.0, "key": None, "data": None})
        financials._cache.update({"key": None, "singles": None})
        rev = [   # 鏡像（千元）：2330 有 11 月（FinMind 還沒有），另外三家給百分位用
            ("2330", "2026-11", 15_000_000, 2, 50), ("2330", "2026-10", 14_700_000, 1, 40), ("2330", "2026-09", 14_600_000, 1, 40),
            ("1101", "2026-11", 1_000_000, -5, -10), ("1101", "2026-10", 1_000_000, -5, -10), ("1101", "2026-09", 1_000_000, -5, -10),
            ("2603", "2026-11", 1_000_000, 0, 10), ("2603", "2026-10", 1_000_000, 0, 10), ("2603", "2026-09", 1_000_000, 0, 10),
            ("3037", "2026-11", 1_000_000, 10, 100), ("3037", "2026-10", 1_000_000, 10, 100), ("3037", "2026-09", 1_000_000, 10, 100),
        ]
        cum = [   # 季報累計（千元）：營收、營業利益、毛利、稅後淨利
            ("2330", "2025Q1", 100, 30, 50, 25), ("2330", "2025Q2", 200, 60, 100, 50), ("2330", "2025Q3", 300, 90, 150, 75),
            ("2330", "2026Q1", 100, 40, 55, 30), ("2330", "2026Q2", 200, 80, 110, 60), ("2330", "2026Q3", 300, 125, 168, 92),
            ("1101", "2025Q3", 300, 15, 60, 9), ("1101", "2025Q2", 200, 10, 40, 6), ("1101", "2026Q2", 200, 10, 40, 6), ("1101", "2026Q3", 300, 12, 55, 7),
        ]
        bars = [("2330", f"2026-11-{d:02d}", c, 12) for d, c in ((18, 1200), (19, 1000), (20, 1050))] + [("2330", "2026-11-17", 1100, 9)]
        with database.get_connection() as c:
            revenue_schema(c)
            financials._schema(c)
            heilong_schema(c)
            c.executemany("INSERT INTO revenue_month (code, ym, market, name, industry, rev, mom, yoy) VALUES (?, ?, 'TSE', '某公司', '半導體業', ?, ?, ?)", rev)
            c.execute("UPDATE revenue_month SET name = '台積電' WHERE code = '2330'")
            c.executemany("INSERT INTO fin_season_cum (code, season, market, name, revenue, op_income, eps, gross, net, updated_at) VALUES (?, ?, 'TSE', 'x', ?, ?, 1, ?, ?, 'x')", cum)
            c.executemany("INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, score2) VALUES (?, ?, 1, 1, 1, ?, 1000, ?)",
                          [(d, code, close, s2) for code, d, close, s2 in bars])

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        financials._cache.update({"key": None, "singles": None})
        self.groups_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_chart_months_closes_and_quarters(self) -> None:
        calls: list = []
        data = module.chart("2330", now=NOW, fetcher=fake_finmind(calls))
        self.assertEqual((data["status"], data["name"], data["latest"], data["group"]), ("ok", "台積電", "2026-11", "半導體"))
        self.assertEqual([c[0] for c in calls], ["TaiwanStockMonthRevenue", "TaiwanStockPrice"])
        self.assertEqual((calls[0][2], calls[1][2]), ("2023-01-01", "2024-01-01"))   # 營收往回 47 個月、股價少抓 12 個月
        months = data["months"]
        self.assertEqual((len(months), months[0]["ym"], months[-1]["ym"]), (34, "2024-02", "2026-11"))
        self.assertEqual(months[-1]["rev"], 150.0)                       # 11 月用鏡像補（15,000,000 千元＝150 億）
        oct_ = months[-2]
        self.assertEqual((oct_["rev"], oct_["mom"], oct_["yoy"]), (147.0, 0.7, 8.9))   # 147 ÷ 146 − 1；147 ÷ 135 − 1
        self.assertEqual(oct_["close"], 1035.0)                          # 10 月最後一個交易日（28 號）收盤
        self.assertEqual(months[-1]["close"], 1036.0)
        self.assertEqual(data["recent"]["yoy"][-1], months[-1]["yoy"])
        q = {x["season"]: x for x in data["quarters"]}
        self.assertEqual((q["2026Q3"]["ym"], q["2026Q3"]["gm"], q["2026Q3"]["om"], q["2026Q3"]["nm"]), ("2026-09", 58.0, 45.0, 32.0))
        self.assertEqual(q["2025Q1"]["ym"], "2025-03")

    def test_scores(self) -> None:
        data = module.chart("2330", now=NOW, fetcher=fake_finmind([]))
        f, t = data["scores"]["fundamental"], data["scores"]["technical"]
        self.assertEqual((f["yoy"], f["mom"]), (67, 67))                 # 4 家裡排第 3（年增 43%、月增 1.3%）
        self.assertEqual(f["margin"], 100)                                # 三率最高、改善最多
        self.assertEqual(f["score"], round((200 / 3 + 200 / 3 + 100) / 3))
        self.assertEqual((t["base"], t["score2"], t["date"]), (75, 12, "2026-11-20"))   # (1050−1000)/(1200−1000)＝25% 的位置
        self.assertEqual(t["score"], round((75 + 12 * 100 / 15) / 2))

    def test_cache_and_failure(self) -> None:
        calls: list = []
        module.chart("2330", now=NOW, fetcher=fake_finmind(calls))
        module.chart("2330", now=NOW + timedelta(hours=5), fetcher=fake_finmind(calls))
        self.assertEqual(len(calls), 2)                                   # 6 小時內不重抓
        module.chart("2330", now=NOW + timedelta(hours=7), fetcher=fake_finmind(calls))
        self.assertEqual(len(calls), 4)

        def broken(dataset, code, start):
            raise OSError("FinMind 掛了")
        old = module.chart("2330", now=NOW + timedelta(hours=20), fetcher=broken)
        self.assertEqual((old["status"], len(old["months"])), ("ok", 34))   # 抓不到用存過的
        only_own = module.chart("1101", now=NOW, fetcher=broken)
        self.assertEqual((only_own["status"], only_own["latest"]), ("ok", "2026-11"))
        self.assertEqual(sum(1 for m in only_own["months"] if m["rev"] is not None), 3)
        self.assertEqual(module.chart("9999", now=NOW, fetcher=broken)["status"], "none")

    def test_endpoint_validation(self) -> None:
        client = TestClient(persistent_app.app)
        self.assertEqual(client.get("/api/hub/revenue-chart").status_code, 422)
        self.assertEqual(client.get("/api/hub/revenue-chart?code=../x").status_code, 422)


if __name__ == "__main__":
    unittest.main()
