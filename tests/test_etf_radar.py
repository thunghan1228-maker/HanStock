"""主動式 ETF 持股雷達：五檔合計今天／一週（依金額）、共同持有、資金潮汐、單檔最新一日／近 5 日／連續同向／權重榜、
個股 × 全部 ETF 進出紀錄與估計成本、反推價補收盤、端點。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import etf_holdings
import etf_radar as module
import heilong_backtest

DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
CLOSE = {"2330": 2500.0, "2368": 1300.0, "2449": 300.0, "3711": 740.0, "1303": 30.0}


def _snap(code: str, day: str, rows: dict[str, int], nav: float = 1e11, units: float = 1e9) -> None:
    total = sum(rows.values()) or 1
    item = {"name": "", "issuer": "", "source": "test", "nav": nav, "units": units,
            "rows": [(s, "", sh, round(sh / total * 100, 2)) for s, sh in rows.items()]}
    etf_holdings.save_snapshot(day, code, item)


class EtfRadarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._cache.update({"key": None, "at": 0.0, "data": None})
        with database.get_connection() as c:
            heilong_backtest._schema(c)
            for d in DAYS:
                for code, close in CLOSE.items():
                    c.execute("INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume) VALUES (?, ?, 1, 1, 1, ?, 1)", (d, code, close))
        # 00981A：金像電 9/30、10/02、10/06、10/07、10/08 連加（10/01、10/05 沒動不算中斷）；日月光 10/06 起連賣；京元 10/08 大減
        a = {"2330": 11_464_000, "2368": 1_000_000, "2449": 12_938_000, "3711": 10_000_000}
        b = {"2330": 2_000_000, "2368": 0, "1303": 0}
        c2 = {"2330": 3_000_000, "9999": 500_000}     # 9999 黑龍表沒有 → 用權重反推價
        for i, d in enumerate(DAYS):
            if d in ("2026-09-30", "2026-10-02", "2026-10-06", "2026-10-07", "2026-10-08"):
                a["2368"] += 300_000
            if d in ("2026-10-06", "2026-10-07", "2026-10-08"):
                a["3711"] -= 500_000
            if d == "2026-10-08":
                a["2449"] -= 4_482_000
                a["2330"] += 360_000
                b["2368"] += 160_000
            if d == "2026-10-02":
                b["1303"] += 2_000_000
            if d == "2026-10-07":
                c2["9999"] += 100_000
            _snap("00981A", d, {k: v for k, v in a.items() if v})
            _snap("00403A", d, {k: v for k, v in b.items() if v}, nav=5e10)
            if d != "2026-10-05":     # 群益那天沒抓到：跟前一份（10/02）比
                _snap("00982A", d, dict(c2), nav=2e10, units=1e9 + i * 1e6)

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()
        module._cache.update({"key": None, "at": 0.0, "data": None})

    def test_overview_today_week_common(self) -> None:
        o = module.overview()
        self.assertEqual((o["date"], o["nFunds"], o["missing"]), ("2026-10-08", 3, ["00991A", "00992A"]))
        buy = {r["code"]: r for r in o["buy"]}
        # 台積電 +360 張 × 2500 ＝ 9 億；金像電 +300＋160 張 × 1300 ＝ 5.98 億，2 檔
        self.assertEqual([r["code"] for r in o["buy"]], ["2330", "2368"])
        self.assertEqual((buy["2330"]["amount"], buy["2330"]["lots"], len(buy["2330"]["funds"])), (9.0, 360.0, 1))
        self.assertEqual((buy["2368"]["amount"], buy["2368"]["lots"], len(buy["2368"]["funds"])), (5.98, 460.0, 2))
        # 京元 -4482 張 × 300 ＝ -13.45 億排第一；日月光 -500 張 × 740 ＝ -3.7 億
        self.assertEqual([(r["code"], r["amount"]) for r in o["sell"]], [("2449", -13.45), ("3711", -3.7)])
        # 一週：往回 5 個資料日（10/01）比到 10/08，只看頭尾
        self.assertEqual((o["wfrom"], o["wto"]), ("2026-10-01", "2026-10-08"))
        wbuy = {r["code"]: r for r in o["wbuy"]}
        self.assertEqual(wbuy["2368"]["lots"], 1360.0)       # 00981A 4×300＋00403A 160
        self.assertEqual(wbuy["1303"]["amount"], 0.6)        # 2000 張 × 30
        self.assertEqual(wbuy["9999"]["lots"], 100.0)        # 黑龍表沒有，仍有反推價可算金額
        self.assertIsNotNone(wbuy["9999"]["amount"])
        self.assertEqual({r["code"]: r["lots"] for r in o["wsell"]}, {"2449": -4482.0, "3711": -1500.0})
        # 共同持有：台積電三檔都有（fullCount 1），其次照家數、再照金額
        self.assertEqual((o["common"][0]["code"], o["common"][0]["n"], o["fullCount"]), ("2330", 3, 1))
        self.assertEqual(o["common"][0]["funds"][0]["rate"], max(f["rate"] for f in o["common"][0]["funds"]))
        # 卡片：每單位淨值、申贖
        g = {f["code"]: f for f in o["funds"]}
        self.assertEqual((g["00981A"]["unitNav"], g["00981A"]["holdings"]), (100.0, 4))
        self.assertEqual(g["00982A"]["unitsDelta"], 1e6)     # 10/07（i=6）→ 10/08（i=7）
        # 潮汐：最近 5 個資料日，累計＝逐日加總
        t = o["tide"]
        self.assertEqual(t["days"], DAYS[-5:])
        hx = next(s for s in t["stocks"] if s["code"] == "3711")
        self.assertEqual(hx["daily"][-3:], [-3.7, -3.7, -3.7])
        self.assertEqual(hx["cum"][-1], -11.1)
        # 回測：指定 10/06
        self.assertEqual(module.overview("2026-10-06")["date"], "2026-10-06")

    def test_fund_views(self) -> None:
        f = module.fund("00981A")
        self.assertEqual((f["date"], f["prev"], f["ndays"], f["span"]), ("2026-10-08", "2026-10-07", 8, [DAYS[0], DAYS[-1]]))
        self.assertEqual([(r["code"], r["delta"], r["act"]) for r in f["diff"]],
                         [("2449", -4_482_000, "減碼"), ("3711", -500_000, "減碼"), ("2330", 360_000, "加碼"), ("2368", 300_000, "加碼")])
        self.assertEqual(f["flow"]["from"], "2026-10-01")
        self.assertEqual({r["code"]: r["delta"] for r in f["flow"]["rows"]}, {"2449": -4_482_000, "3711": -1_500_000, "2330": 360_000, "2368": 1_200_000})
        up = {r["code"]: r for r in f["streak"]["up"]}
        dn = {r["code"]: r for r in f["streak"]["dn"]}
        # 金像電連 5 次，從 9/30 到 10/08 跨 7 個資料日（10/01、10/05 沒動不算中斷）
        self.assertEqual((up["2368"]["moves"], up["2368"]["span"], up["2368"]["delta"], up["2368"]["since"]), (5, 7, 1_500_000, "2026-09-30"))
        self.assertEqual((dn["3711"]["moves"], dn["3711"]["span"]), (3, 3))
        self.assertNotIn("2449", dn)           # 只減一次
        self.assertNotIn("2330", up)           # 只加一次
        self.assertEqual(f["top"][0]["code"], "2330")         # 京元減完剩 8,456 張，台積電 11,824 張權重最大
        self.assertEqual([(r["code"], r["act"]) for r in module.fund("00403A")["diff"]], [("2368", "新進")])
        self.assertEqual([(r["code"], r["act"]) for r in module.fund("00403A", "2026-10-02")["diff"]], [("1303", "新進")])
        self.assertEqual(module.fund("00403A", "2026-09-30")["diff"], [])
        with self.assertRaises(LookupError):
            module.fund("0050")
        with self.assertRaises(LookupError):
            module.fund("00991A")

    def test_stock_track(self) -> None:
        s = module.stock("2368")
        self.assertEqual((s["code"], s["held"], [x["code"] for x in s["funds"]]), ("2368", 2, ["00981A", "00403A"]))
        self.assertEqual(s["rows"][0]["date"], "2026-10-08")
        self.assertEqual(s["rows"][0]["cells"]["00403A"]["act"], "新進")
        self.assertEqual(s["rows"][0]["total"], 460_000)
        self.assertEqual([r["date"] for r in s["rows"]], ["2026-10-08", "2026-10-07", "2026-10-06", "2026-10-02", "2026-09-30"])
        cost = s["hold"]["00981A"]["cost"]
        self.assertEqual((cost["avg"], cost["pnl"]), (1300.0, 0.0))      # 收盤一直 1300，成本也是 1300
        self.assertEqual(module.stock("2449")["hold"]["00981A"]["share"], 8_456_000)
        with self.assertRaises(LookupError):
            module.stock("2603")

    def test_endpoints(self) -> None:
        from fastapi.testclient import TestClient

        import persistent_app

        client = TestClient(persistent_app.app)
        r = client.get("/api/hub/etf-radar")
        self.assertEqual((r.status_code, r.json()["date"]), (200, "2026-10-08"), r.text[:300])
        self.assertEqual(client.get("/api/hub/etf-radar", params={"date": "2026-10-02"}).json()["date"], "2026-10-02")
        self.assertEqual(client.get("/api/hub/etf-radar/fund", params={"code": "00981A"}).json()["holdings"], 4)
        self.assertEqual(client.get("/api/hub/etf-radar/fund", params={"code": "XXX"}).status_code, 404)
        self.assertEqual(client.get("/api/hub/etf-radar/stock", params={"q": "2368"}).json()["held"], 2)
        self.assertEqual(client.get("/api/hub/etf-radar/stock", params={"q": "2603"}).status_code, 404)
        with patch.object(persistent_app, "run_etf_collect", return_value={"added": []}) as run:
            self.assertEqual(client.post("/api/hub/etf/collect", params={"days": 60}).status_code, 200)
            run.assert_called_once_with(limit=60)

    def test_empty(self) -> None:
        with database.get_connection() as c:
            c.execute("DELETE FROM etf_snapshots")
            c.execute("DELETE FROM etf_holdings")
        module._cache.update({"key": None, "at": 0.0, "data": None})
        self.assertEqual(module.overview()["status"], "missing")


if __name__ == "__main__":
    unittest.main()
