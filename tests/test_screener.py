"""選股系統：期交所標的清單解析、各條件篩選（均線、籌碼、ETF、法人估量、雙劍、處置倒數、股期、排除處置、K棒）、D+1 績效、端點。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import heilong_backtest
import screener as module

D = ["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
TAIFEX = """
<table><tr><td>CD</td><td>台灣積體電路製造股份有限公司</td><td>2330</td><td>台積電</td><td><span class="sr-only">是股票期貨標的</span></td>
<td></td><td></td><td></td><td></td><td></td><td></td><td>2,000</td><td>8:45~13:45</td></tr>
<tr><td>QF</td><td>台灣積體電路製造股份有限公司</td><td>2330</td><td>台積電</td><td><span class="sr-only">是股票期貨標的</span></td>
<td></td><td></td><td></td><td></td><td></td><td></td><td>100</td><td>8:45~13:45</td></tr>
<tr><td>CA</td><td>南亞塑膠</td><td>1111</td><td>一號</td><td><span class="sr-only">是股票期貨標的</span></td>
<td></td><td></td><td></td><td></td><td></td><td></td><td>2,000</td><td>8:45~13:45</td></tr>
<tr><td>XX</td><td>只有選擇權</td><td>9999</td><td>九號</td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td><td>2,000</td><td>8:45~13:45</td></tr></table>
"""


class FakeRadar:
    dates = ["2026-10-02", "2026-09-24", "2026-09-18"]    # 新到舊
    chips = {"1111": {"2026-10-02": 6.0, "2026-09-24": 5.0}, "2222": {"2026-10-02": 2.0, "2026-09-24": 4.5}, "3333": {"2026-10-02": 4.5}}

    def lists(self, week: str):
        values = [(c, per[week]) for c, per in self.chips.items() if week in per]
        return [c for c, v in sorted(values, key=lambda cv: -cv[1]) if v >= 4], []


class ParseTests(unittest.TestCase):
    def test_taifex(self) -> None:
        std, mini = module.parse_taifex_stock_lists(TAIFEX)
        self.assertEqual((std, mini), ({"2330", "1111"}, {"2330"}))


class ScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        # (開, 高, 收, 量張, 漲跌%, 分數)：資料日 10/05
        bars = {
            "1111": [(100, 101, 100, 1000, 0.0, 14), (100, 106, 105, 2000, 5.0, 15), (105, 112, 110, 1500, 4.76, 15), (110, 111, 99, 1000, -10.0, 13), (99, 100, 100, 1000, 1.0, 13)],
            "2222": [(50, 51, 50, 1000, 0.0, 12), (51, 52, 49, 1000, -2.0, 12), (49, 50, 48, 1000, -2.04, 11), (48, 48, 48, 1000, 0.0, 11), (48, 48, 48, 1000, 0.0, 11)],
            "3333": [(10, 10, 10, 1000, 0.0, 8), (10, 11, 10.5, 1000, 5.0, 8), (10.5, 11, 11, 1000, 4.76, 8), (11, 11, 11, 1000, 0.0, 8), (11, 11, 11, 1000, 0.0, 8)],
            "4444": [(20, 20, 20, 500, 0.0, None)] * 5,
            "0050": [(150, 150, 150, 9000, 0.0, 9)] * 5,
        }
        with database.get_connection() as c:
            heilong_backtest._schema(c)
            for code, series in bars.items():
                for d, (o, h, cl, v, chg, sc) in zip(D, series):
                    c.execute("INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, change_pct, score2) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (d, code, o, h, min(o, cl), cl, v, chg, sc))
            c.execute("INSERT OR REPLACE INTO stocks (stock_code, stock_name, market, updated_at) VALUES ('4444', '四號', 'TSE', 'x')")
            from chips_daily import _schema as chips_schema
            chips_schema(c)
            for d, net in ((D[0], 300_000), (D[1], 500_000)):   # 1111：兩天各買 300／500 張
                c.execute("INSERT INTO institutional_daily (trade_date, stock_code, market, stock_name, foreign_net, trust_net, dealer_net, total_net, source, updated_at) VALUES (?, '1111', 'TSE', '一號', ?, 0, 0, ?, 't', 'x')", (d, net, net))
            c.execute("INSERT INTO institutional_daily (trade_date, stock_code, market, stock_name, foreign_net, trust_net, dealer_net, total_net, source, updated_at) VALUES (?, '2222', 'TSE', '二號', -100000, 0, 0, -100000, 't', 'x')", (D[1],))
            from etf_holdings import _schema as etf_schema
            etf_schema(c)
            for etf in ("00981A", "00991A"):
                c.execute("INSERT INTO etf_snapshots (etf_code, trade_date, rows, updated_at) VALUES (?, ?, 1, 'x')", (etf, D[0]))
                c.execute("INSERT INTO etf_holdings (etf_code, trade_date, stock_code, stock_name, shares) VALUES (?, ?, '1111', '一號', 1000)", (etf, D[0]))
            from disposition_jail import _schema as jail_schema
            jail_schema(c)
            c.execute("INSERT INTO jail_punish (code, start_date, end_date, market, name, announce_date) VALUES ('2222', '2026-09-30', '2026-10-06', 'TSE', '二號', '2026-09-29')")
            c.execute("INSERT INTO jail_punish (code, start_date, end_date, market, name, announce_date) VALUES ('3333', '2026-10-06', '2026-10-19', 'TSE', '三號', '2026-10-05')")
        groups = {"甲": [("1111", "一號"), ("2222", "二號")], "千元": [("3333", "三號")], "股期標的": [("1111", "一號")]}
        self.patches = [
            patch.object(module, "STOCK_GROUPS", groups),
            patch("chip_radar.load_radar", lambda: FakeRadar()),
            patch("ma_rank.regulars", lambda days, top, date: [("1111", 9), ("3333", 4), ("2222", 2)]),
        ]
        for p in self.patches:
            p.start()
        module._cache.clear()
        module._futures.update({"at": 0.0, "std": None, "mini": None, "source": None})
        self.fut = patch.object(module, "_fetch_text", lambda url, timeout=30: TAIFEX)
        self.fut.start()

    def tearDown(self) -> None:
        self.fut.stop()
        for p in self.patches:
            p.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        module._cache.clear()

    def codes(self, raw: dict, date: str = D[1]) -> list[str]:
        return [r["code"] for r in module.screen(raw, date)["rows"]]

    def test_all_rows_and_columns(self) -> None:
        out = module.screen({}, D[1])
        self.assertEqual((out["date"], out["latest"], out["universe"], out["next"], out["latestDate"]), (D[1], D[4], 3, [D[2], D[3]], D[4]))
        # 照分數排；0050 不算；4444 不在族群、沒股期／ETF／處置／籌碼榜 → 不在範圍；3333 只在千元但明天起處置 → 在範圍
        self.assertEqual([r["code"] for r in out["rows"]], ["1111", "2222", "3333"])
        one = out["rows"][0]
        self.assertEqual((one["name"], one["grp"], one["score"], one["k"], one["chg"]), ("一號", "甲", 15, "red", 5.0))
        self.assertEqual((one["chip"], one["chipOn"], one["maHits"], one["chipHits"], one["etf"]), (6.0, True, 9, 2, 2))
        self.assertEqual((one["fut"], one["mini"], one["futLabel"]), (True, False, "期"))
        self.assertEqual(one["inst3"], {"net": 800, "pct": round(800 / 3000 * 100, 1), "buy": True})   # 只有兩天有量（10/02、10/05）
        self.assertEqual((one["d1"], one["d1h"], one["d2"], one["perf"]), (4.76, 6.67, -5.71, -4.76))
        two = next(r for r in out["rows"] if r["code"] == "2222")
        self.assertEqual((two["dispo"], two["upcoming"], two["k"], two["inst5"]["buy"]), (2, False, "black", False))   # 10/05、10/06 → 2
        three = next(r for r in out["rows"] if r["code"] == "3333")
        self.assertEqual((three["grp"], three["dispo"], three["upcoming"], three["chipOn"]), ("", None, True, True))   # 千元不當族群
        self.assertEqual(out["chipWeek"], "2026-10-02")
        self.assertEqual(module.screen({}, D[4])["latestDate"], None)

    def test_conditions(self) -> None:
        self.assertEqual(self.codes({"score": 12}), ["1111", "2222"])
        self.assertEqual(self.codes({"chip": 5}), ["1111"])            # 3333 4.5 有上榜但不到 5
        self.assertEqual(self.codes({"chip": 0}), ["1111", "3333"])    # 2222 2.0 沒上榜（>4 才算）
        self.assertEqual(self.codes({"etf": 1}), ["1111"])
        self.assertEqual(self.codes({"inst3": 5, "inst5": 10}), ["1111"])
        self.assertEqual(self.codes({"inst3": 30}), [])
        self.assertEqual(self.codes({"dispo": 5}), ["2222"])
        self.assertEqual(self.codes({"dispo": 1}), [])
        self.assertEqual(self.codes({"exdispo": "1"}), ["1111"])   # 2222 處置中、3333 明天起處置
        self.assertEqual(self.codes({"fut": "1"}), ["1111"])
        self.assertEqual(self.codes({"mini": "1"}), [])
        self.assertEqual(self.codes({"k": "black"}), ["2222"])
        self.assertEqual(self.codes({"k": "red", "kmin": 5}), ["1111", "3333"])
        self.assertEqual(self.codes({"kmin": -10, "kmax": 3}), ["2222"])
        # 雙劍：均線常客 [1111, 3333, 2222]、籌碼常客（9 週買超榜前 10）[1111 兩週, 2222 一週（9/24）, 3333 一週（10/02）]
        self.assertEqual(self.codes({"sword": 60}), ["1111", "2222", "3333"])
        self.assertEqual(self.codes({"sword": 1}), ["1111"])
        # 創高黑龍那組：均線 ≥10、排除處置、黑K −10%～3%
        self.assertEqual(self.codes({"score": 10, "exdispo": "1", "k": "black", "kmin": -10, "kmax": 3}, D[3]), ["1111"])
        with self.assertRaises(ValueError):
            module.screen({"k": "blue"})
        with self.assertRaises(ValueError):
            module.screen({"score": "x"})
        with self.assertRaises(LookupError):
            module.screen({}, "2026-01-02")

    def test_futures_fallback(self) -> None:
        module._futures.update({"at": 0.0, "std": None})
        with patch.object(module, "_fetch_text", side_effect=OSError("blocked")):
            std, mini, source = module.futures_sets()
        self.assertEqual((source, "1111" in std, "2330" in mini), ("內建清單", True, True))

    def test_stock_and_endpoints(self) -> None:
        self.assertEqual(module.stock("2222", D[1])["row"]["dispo"], 2)
        self.assertEqual(module.stock("4444", D[1])["row"]["inUni"], False)     # 個股彙整不限範圍
        with self.assertRaises(LookupError):
            module.stock("9999")
        from fastapi.testclient import TestClient

        import persistent_app

        client = TestClient(persistent_app.app)
        r = client.get("/api/hub/screener", params={"score": 12, "date": D[1]})
        self.assertEqual((r.status_code, [x["code"] for x in r.json()["rows"]]), (200, ["1111", "2222"]), r.text[:300])
        self.assertEqual(client.get("/api/hub/screener", params={"k": "x"}).status_code, 422)
        self.assertEqual(client.get("/api/hub/screener", params={"date": "2026-01-02"}).status_code, 404)
        r = client.get("/api/hub/screener/stock", params={"code": "1111", "date": D[1]})
        self.assertEqual((r.status_code, r.json()["row"]["etf"]), (200, 2))
        self.assertEqual(client.get("/api/hub/screener/stock", params={"code": "9999"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
