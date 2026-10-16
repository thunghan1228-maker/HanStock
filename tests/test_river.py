"""估值河流圖・本站版：分水嶺＝近 4 季 EPS × 同族群本益比中位數、四區切法、虧損／獲利太薄、河道歷史跳季、⭐均線≥10×便宜區、端點。"""

from __future__ import annotations

import tempfile
from datetime import datetime
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import heilong_backtest
import financials
import river as module
from fundamentals_daily import _schema as fund_schema, save_pe

GROUPS = {
    "被動元件": [("2492", "華新科"), ("2327", "國巨"), ("2472", "立隆電"), ("8042", "金山電")],
    "記憶體": [("2408", "南亞科"), ("2344", "華邦電")],          # 只有 2 檔有本益比 → 用全市場中位數
    "股期標的": [("2327", "國巨")],
}
D = "2026-10-08"


class RiverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        self.groups = patch.object(module, "STOCK_GROUPS", GROUPS)
        self.groups.start()
        database.initialize_database()
        # 本益比：被動 40／30／20／10（中位數 25）；記憶體 20、10；9999 虧損；1111 本益比 300（太薄）
        pes = {"2492": 40.0, "2327": 30.0, "2472": 20.0, "8042": 10.0, "2408": 20.0, "2344": 10.0, "9999": None, "1111": 300.0}
        closes = {"2492": 400.0, "2327": 300.0, "2472": 100.0, "8042": 50.0, "2408": 500.0, "2344": 100.0, "9999": 30.0, "1111": 90.0}
        scores = {"2492": 12, "2327": 9, "2472": 15, "8042": 11, "2408": 14, "2344": 13, "9999": 5, "1111": 10}
        with database.get_connection() as c:
            fund_schema(c)
            heilong_backtest._schema(c)
            for code, close in closes.items():
                c.execute("INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, score2) VALUES (?, ?, 1, 1, 1, ?, 1, ?)",
                          (D, code, close, scores[code]))
            # 華新科的日K與本益比歷史：8/31 收 300、本益比 50（EPS4 6）；10/08 收 400、本益比 40（EPS4 10）→ 河道跳一次
            for d, close in (("2026-08-28", 290.0), ("2026-08-31", 300.0), ("2026-09-30", 330.0), (D, 400.0)):
                c.execute("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES ('2492', ?, ?, ?, ?, ?, 1000)",
                          (d + "T00:00:00", close, close, close, close))
            c.execute("INSERT OR REPLACE INTO stocks (stock_code, stock_name, market, updated_at) VALUES ('2492', '華新科', 'TSE', 'x')")
        save_pe("2026-08-31", "TSE", [{"code": "2492", "pe": 50.0, "pbr": 2.0, "yield": None}], "twse-history")
        save_pe("2026-09-30", "TSE", [{"code": "2492", "pe": 55.0, "pbr": 2.0, "yield": None}], "twse-history")
        save_pe(D, "TSE", [{"code": c, "pe": v, "pbr": 2.0 if v else None, "yield": None} for c, v in pes.items()], "twse")
        with database.get_connection() as c:
            c.execute("INSERT INTO stock_revenue_monthly (stock_code, ym, revenue, yoy_pct, mom_pct, market, updated_at) VALUES ('2492', '2026-08', 4568665, 45.5, 2.8, 'TSE', 'x')")
            c.execute("INSERT INTO stock_revenue_monthly (stock_code, ym, revenue, yoy_pct, mom_pct, market, updated_at) VALUES ('2492', '2026-07', 4440000, 42.0, 1.0, 'TSE', 'x')")
        module._cache.update({"key": None, "at": 0.0, "data": None})
        financials._cache.update({"key": None, "singles": None})

    def tearDown(self) -> None:
        self.groups.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        module._cache.update({"key": None, "at": 0.0, "data": None})

    def test_zone_boundaries(self) -> None:
        s = 100.0
        self.assertEqual([module.zone_of(p, s) for p in (61.7, 61.8, 79.9, 80, 99.9, 100, 119.9, 120, 138.1, 138.2)],
                         [0, 1, 1, 2, 2, 3, 3, 4, 4, 5])

    def test_rows(self) -> None:
        rows = module.load()["rows"]
        hx = rows["2492"]          # EPS4 10 × 被動中位數 25 → 分水嶺 250；400 是 +60% → 超昂貴
        self.assertEqual((hx["eps4"], hx["ref"], hx["refGroup"], hx["s"], hx["zone"], hx["zoneName"], hx["dist"]),
                         (10.0, 25.0, "被動元件", 250.0, 5, "超昂貴", 60.0))
        self.assertEqual(hx["bounds"], [154.5, 200.0, 250.0, 300.0, 345.5])
        self.assertEqual((rows["8042"]["zoneName"], rows["8042"]["dist"]), ("跌破特價", -60.0))   # EPS4 5 × 25 ＝ 125，50 只有 0.4 倍
        self.assertEqual(rows["2472"]["s"], 125.0)         # EPS4 5 × 25
        self.assertEqual(rows["2472"]["zone"], 2)          # 100 ÷ 125 ＝ 0.8 → 便宜
        # 記憶體只有 2 檔有本益比 → 全市場中位數（20、30、40、10、20、10、300 不算 → 中位數 20）
        self.assertEqual((rows["2408"]["refGroup"], rows["2408"]["ref"]), ("全市場", 20.0))
        self.assertEqual((rows["9999"]["noVal"], rows["9999"]["why"]), (True, "loss"))
        self.assertEqual((rows["1111"]["noVal"], rows["1111"]["why"]), (True, "thin"))
        self.assertEqual(module.zones()["2472"], 2)

    def test_query_history_and_lists(self) -> None:
        out = module.query("華新科")
        st = out["stock"]
        self.assertEqual((st["code"], st["bps"], st["fund"]["rev"][0]), ("2492", 200.0, {"ym": "2026-08", "rev": 45.7, "yoy": 45.5}))
        chart = st["chart"]
        self.assertEqual(chart["dates"], ["2026-08-28", "2026-08-31", "2026-09-30", D])
        # 8/28 還沒有本益比 → 沒有河道；8/31 EPS4 6 → 分水嶺 150；9/30 本益比 55 → EPS4 6（沒變）；10/08 EPS4 10 → 250
        self.assertEqual([b for b in chart["bands"][2]], [None, 150.0, 150.0, 250.0])
        self.assertEqual([x["start"] for x in chart["steps"]], ["2026-08-31", D])
        self.assertEqual(module.query("2472")["stock"]["zoneName"], "便宜")
        with self.assertRaises(LookupError):
            module.query("不存在")
        ma = module.ma10()
        self.assertEqual([r["code"] for r in ma["rows"]], ["2472", "2344", "8042"])   # 均線≥10 又在便宜側的族群成員，分數高的先；國巨 9 分不算
        self.assertEqual({x["code"] for x in module.stock_list()} >= {"2492", "2472"}, True)

    def test_group_map(self) -> None:
        # 內部河流：同族群同一把 PE 尺（被動中位數 25）；虧損／太薄／沒資料的列飛魚（nv），由貴到便宜排
        groups = dict(GROUPS, 被動元件=GROUPS["被動元件"] + [("9999", "虧損股"), ("1111", "太薄股"), ("3026", "沒資料")],
                      記憶體=GROUPS["記憶體"] + [("5555", "薄利股")])
        with database.get_connection() as c:
            c.execute("INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, score2) VALUES (?, '5555', 1, 1, 1, 120, 1, 8)", (D,))
        save_pe(D, "TSE", [{"code": "5555", "pe": 120.0, "pbr": None, "yield": None}], "twse")
        module._cache.update({"key": None, "at": 0.0, "data": None})
        with patch.object(module, "STOCK_GROUPS", groups):
            m = module.group_map("被動元件")
            self.assertEqual((m["ref"], m["refFrom"]), (25.0, "族群"))
            self.assertEqual([(r["code"], r.get("dist"), r.get("zone")) for r in m["rows"] if not r["nv"]],
                             [("2492", 60.0, 5), ("2327", 20.0, 4), ("2472", -20.0, 2), ("8042", -60.0, 0)])
            self.assertEqual([(r["code"], r["why"]) for r in m["rows"] if r["nv"]], [("1111", "thin"), ("3026", "none"), ("9999", "loss")])
            mem = module.group_map("記憶體")          # 記憶體現在 3 檔有本益比 → 族群中位數 20
            self.assertEqual((mem["ref"], mem["refFrom"]), (20.0, "族群"))
            weak = {r["code"]: r["weak"] for r in mem["rows"]}
            self.assertEqual((weak["5555"], weak["2408"]), (True, False))   # 本益比 100 倍以上＝基準參考弱
            self.assertEqual(module.query("華新科")["stock"]["groupMap"]["group"], "被動元件")
            self.assertIsNone(module.group_map("股期標的"))

    def test_financial_eps_and_top_queries(self) -> None:
        # 金山電有財報：單季 25Q3 1.0、25Q4 0.5、26Q1 0.5、26Q2 0.5 → 近 4 季 2.5，收 50 → 本益比 20（交易所的 10 不用）
        cum = {"2025Q2": (100.0, 10.0, 0.5), "2025Q3": (200.0, 20.0, 1.5), "2025Q4": (300.0, 30.0, 2.0),
               "2026Q1": (100.0, 10.0, 0.5), "2026Q2": (220.0, 40.0, 1.0)}
        for season, (rev, opi, eps) in cum.items():
            financials.save_season(season, [{"code": "8042", "name": "金山電", "market": "otc", "revenue": rev, "opIncome": opi, "eps": eps}], "t")
        rows = module.load()["rows"]
        jin = rows["8042"]
        self.assertEqual((jin["pe"], jin["eps4"], jin["epsFrom"], jin["epsAsOf"]), (20.0, 2.5, "財報", "26Q2"))
        self.assertEqual((jin["ref"], jin["dist"], jin["zone"]), (25.0, -20.0, 2))     # 被動本益比 40／30／20／20 → 中位數 25
        self.assertEqual(rows["2492"]["epsFrom"], "本益比")
        st = module.query("8042")["stock"]
        self.assertEqual(st["quarters"], [{"label": "25Q3", "eps": 1.0}, {"label": "25Q4", "eps": 0.5}, {"label": "26Q1", "eps": 0.5}, {"label": "26Q2", "eps": 0.5}])
        self.assertEqual(st["margins"], [{"label": "26Q1", "margin": 10.0}, {"label": "26Q2", "margin": 25.0}])
        # 本週查詢排行
        now = datetime(2026, 10, 16, 21, 0)
        self.assertTrue(module.record_hit("2492", now))
        self.assertTrue(module.record_hit("2492", now))
        self.assertTrue(module.record_hit("8042", now))
        self.assertFalse(module.record_hit("ZZZZ", now))
        self.assertTrue(module.record_hit("2472", datetime(2026, 10, 9, 21, 0)))    # 8 天前，不算
        top = module.top_queries(now=now)
        self.assertEqual([(r["code"], r["count"]) for r in top["rows"]], [("2492", 2), ("8042", 1)])
        self.assertEqual((top["rows"][0]["yoy"], top["rows"][0]["eps"]), (45.5, None))
        self.assertEqual((top["rows"][1]["eps"], top["rows"][1]["epsLabel"], top["rows"][1]["zoneName"]), (0.5, "26Q2", "便宜"))

    def test_endpoints(self) -> None:
        from fastapi.testclient import TestClient

        import persistent_app

        client = TestClient(persistent_app.app)
        r = client.get("/api/hub/river", params={"q": "2492"})
        self.assertEqual((r.status_code, r.json()["stock"]["zoneName"]), (200, "超昂貴"), r.text[:300])
        self.assertEqual(client.get("/api/hub/river", params={"q": "zzz"}).status_code, 404)
        self.assertEqual(client.get("/api/hub/river/ma10").json()["count"], 3)
        self.assertTrue(client.get("/api/hub/river/list").json()["stocks"])
        hit = client.get("/api/hub/river/hit", params={"code": "2492"})
        self.assertEqual((hit.json()["counted"], hit.headers["cache-control"]), (True, "no-store"))
        self.assertEqual(client.get("/api/hub/river/top").json()["rows"][0]["code"], "2492")


if __name__ == "__main__":
    unittest.main()
