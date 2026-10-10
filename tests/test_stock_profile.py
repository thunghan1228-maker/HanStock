"""個股研究補強：產業別解析、族群白話與同業、季報解析（三率、年增、轉盈虧）、近四季 EPS 與本益比、快取、端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import persistent_app
import stock_profile as module
from stock_groups import STOCK_GROUPS

TW = module.TW_TZ
INFO = {"data": [
    {"stock_id": "3037", "stock_name": "欣興", "industry_category": "電子工業", "type": "twse"},
    {"stock_id": "3037", "stock_name": "欣興", "industry_category": "電子零組件業", "type": "twse"},
    {"stock_id": "2313", "stock_name": "華通", "industry_category": "電子零組件業", "type": "twse"},
    {"stock_id": "0050", "stock_name": "元大台灣50", "industry_category": "ETF", "type": "twse"},
]}


def fin_rows():
    rows = []
    for d, rev, gp, op, net, eps in (("2025-03-31", 300e8, 45e8, 15e8, 10e8, 0.5), ("2025-06-30", 320e8, 50e8, 18e8, 0.2e8, 0.05),
                                     ("2025-09-30", 330e8, 52e8, 20e8, 12e8, 0.8), ("2025-12-31", 340e8, 55e8, 22e8, -1e8, -0.1),
                                     ("2026-03-31", 374e8, 67e8, 27e8, 50e8, 3.28), ("2026-06-30", 428.9e8, 106e8, 66e8, 131e8, 8.45)):
        for t, v in (("Revenue", rev), ("GrossProfit", gp), ("OperatingIncome", op), ("EquityAttributableToOwnersOfParent", net), ("EPS", eps)):
            rows.append({"date": d, "type": t, "value": v})
    return {"data": rows}


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        with database.get_connection() as c:
            c.execute("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES ('3037', '2026-10-08T00:00:00', 1, 1, 1, 241.5, 1)")
        self.calls: list[dict] = []

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def fetcher(self, params):
        self.calls.append(params)
        return INFO if params["dataset"] == "TaiwanStockInfo" else fin_rows()

    def test_parse(self) -> None:
        info = module.parse_info(INFO)
        self.assertEqual(info["3037"], {"name": "欣興", "industry": "電子零組件業", "market": "TSE"})
        self.assertNotIn("0050", info)
        q = module.parse_financials(fin_rows())
        self.assertEqual([x["label"] for x in q], ["2026Q2", "2026Q1", "2025Q4", "2025Q3", "2025Q2", "2025Q1"])
        top = q[0]
        self.assertEqual((top["revenue"], top["gross"], top["op"], top["eps"], top["revYoY"], top["epsYoY"], top["epsTurn"]),
                         (428.9, 24.71, 15.39, 8.45, 34.0, None, None))       # 去年同季 EPS 0.05 太小，不算 %
        self.assertEqual(q[1]["epsYoY"], 556.0)
        self.assertEqual(q[2]["epsTurn"], None)                               # 2024Q4 沒資料
        self.assertEqual(module.parse_financials({"data": []}), [])

    def test_profile_and_cache(self) -> None:
        now = datetime(2026, 10, 10, 20, 0, tzinfo=TW)
        p = module.profile("3037", fetcher=self.fetcher, now=now)
        self.assertEqual((p["status"], p["name"], p["industry"], p["market"]), ("ok", "欣興", "電子零組件業", "TSE"))
        pcb = next(g for g in p["groups"] if g["group"] == "PCB")
        self.assertTrue(pcb["text"])
        self.assertNotIn("3037", [m["code"] for m in pcb["members"]])
        self.assertEqual([x["code"] for x in p["peers"]], ["2313"] if "2313" not in {str(c) for g in p["groups"] for c, _ in STOCK_GROUPS[g["group"]]} else [])
        self.assertEqual((p["ttmEps"], p["close"], p["pe"]), (round(8.45 + 3.28 - 0.1 + 0.8, 2), 241.5, round(241.5 / 12.43, 1)))
        n = len(self.calls)
        module.profile("3037", fetcher=self.fetcher, now=now + timedelta(days=1))           # 快取內不重抓
        self.assertEqual(len(self.calls), n)
        module.profile("3037", fetcher=self.fetcher, now=now + timedelta(days=4))           # 季報過期重抓，產業別還沒過期
        self.assertEqual([c["dataset"] for c in self.calls[n:]], ["TaiwanStockFinancialStatements"])

        def broken(params):
            raise OSError("down")
        stale = module.profile("3037", fetcher=broken, now=now + timedelta(days=30))        # 抓不到用舊的
        self.assertEqual((stale["industry"], len(stale["quarters"])), ("電子零組件業", 6))
        self.assertEqual(len(stale["errors"]), 2)

    def test_every_group_has_intro(self) -> None:
        for name in STOCK_GROUPS:
            if name != "股期標的":
                self.assertTrue(module.GROUP_INTROS.get(name), name)

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        with patch.object(module, "_default_fetcher", self.fetcher):
            r = client.get("/api/hub/stock-profile?code=3037")
        self.assertEqual((r.status_code, r.json()["name"]), (200, "欣興"))
        self.assertEqual(client.get("/api/hub/stock-profile?code=!!").status_code, 422)


if __name__ == "__main__":
    unittest.main()
