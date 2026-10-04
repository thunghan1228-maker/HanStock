"""籌碼暴增雷達：籌碼% 公式（對照莊爸截圖的真實數字）、本週買超／賣超榜與 ⭐、族群前 5 平均、連續增、累積榜、個股查詢。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chip_radar as module
import database
import fundamentals_daily

WEEKS = ["2026-08-21", "2026-08-28", "2026-09-04", "2026-09-11", "2026-09-18", "2026-09-24", "2026-10-02"]   # 舊到新
TOTAL = 100_000_000


class FormulaTests(unittest.TestCase):
    def test_matches_zhuang_screenshots(self) -> None:
        # 希華 2484 10/02：400 張以上 41,041,075 → 52,917,150 股，總股數 159,421,022 → x 7.45 → 3√7.45 ＝ 8.19
        self.assertEqual(module.chip_value(52_917_150, 41_041_075, 159_421_022), (7.45, 8.19))
        # 環球晶 6488 10/02 增資那週：分母用這週的總股數 → x 10.10 → 9.53
        self.assertEqual(module.chip_value(421_223_173, 367_860_151, 528_113_725), (10.1, 9.53))
        # 中華化 1727 10/02：大戶變少照原樣 −7.28（截圖 −7.3）
        self.assertEqual(module.chip_value(78_869_046, 88_188_354, 128_019_559), (-7.28, -7.28))
        # 聯一光電 3441 9/11：只多 0.01% → 3√0.01 ＝ 0.3
        self.assertEqual(module.chip_value(100_000 + 4_004, 100_000, 40_039_920)[1], 0.3)
        self.assertIsNone(module.chip_value(1, 1, 0))


def big_for(xs: list[float]) -> list[int]:
    """照每週的 x（%）排出 400 張以上股數（舊到新），第一週 30%。"""
    out = [30_000_000]
    for x in xs:
        out.append(out[-1] + round(x / 100 * TOTAL))
    return out


class RadarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._cache.update({"at": 0.0, "key": None, "radar": None})
        groups = {"石英": [("2484", "希華"), ("3042", "晶技"), ("8182", "加高"), ("3221", "台嘉碩"), ("8289", "泰藝"), ("6174", "安碁")],
                  "被動元件": [("6127", "九豪"), ("3236", "千如")], "股期標的": [("2484", "希華")]}
        self.groups_patch = patch.object(module, "STOCK_GROUPS", groups)
        self.groups_patch.start()
        # 每檔每週的 x（8/28 起六週，對 8/21）
        series = {
            "2484": [-0.46, 1.0, 0.04, 1.42, 1.5, 7.45],     # 希華：近三週都增、六週五增
            "3042": [0.5, 0.6, 0.7, 0.8, 0.9, 2.43],          # 晶技：六週都增
            "8182": [0.0, 0.0, 0.0, 0.0, 0.1, 0.45],
            "3221": [0.0, 0.0, 0.0, 0.0, 0.0, -0.73],
            "8289": [0.0, 0.0, 0.0, 0.0, 0.0, -1.26],
            "6174": [0.0, 0.0, 0.0, 0.0, 0.0, 1.11],
            "6127": [3.0, 0.0, 0.0, 0.0, 2.0, 3.19],          # 九豪：9/24、10/02 連兩週上買超榜 → ⭐
            "3236": [0.0, 0.0, 0.0, 0.0, 0.0, 2.85],
            "1727": [0.0, 0.0, 0.0, 0.0, -2.0, -7.28],        # 中華化：連兩週賣超榜 → ⭐
            "0050": [9.0, 9.0, 9.0, 9.0, 9.0, 9.0],           # ETF 不算
            "4806": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],           # 桂田文創：10/02 減資 30%（下面改股數）
            "3013": [0.0, 0.0, 0.0, 0.0, 0.0, 4.18],          # 晟銘電：10/02 股本 +4%
            "2033": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],           # 佳大：10/02 私募增資 +50%（下面改股數）
            "2601": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],           # 益航：10/02 股份轉換、總股數 −16%；上週只有 12～15 級（舊鏡像沒合計）
        }
        rows_by_week: dict[str, list[tuple]] = {d: [] for d in WEEKS}
        for code, xs in series.items():
            bigs = big_for(xs)
            for d, big in zip(WEEKS, bigs):
                rows_by_week[d] += [(code, 12, 1, big // 4, 0.0), (code, 15, 1, big - big // 4, 0.0), (code, 17, 1000, TOTAL, 100.0)]
        # 減資 30%：總股數 1 億 → 7000 萬，大戶 4000 萬 → 1200 萬 → 籌碼% −40；股本 +4%：總股數 1.04 億
        def edit(rows: list[tuple], code: str, total: int, big: int | None = None) -> list[tuple]:
            out = [r for r in rows if r[0] != code]
            b = big if big is not None else next(r[3] for r in rows if r[0] == code and r[1] == 15) + next(r[3] for r in rows if r[0] == code and r[1] == 12)
            return out + [(code, 12, 1, b // 4, 0.0), (code, 15, 1, b - b // 4, 0.0), (code, 17, 1000, total, 100.0)]
        rows_by_week["2026-10-02"] = edit(rows_by_week["2026-10-02"], "4806", 70_000_000, 30_000_000 - 28_000_000)
        rows_by_week["2026-10-02"] = edit(rows_by_week["2026-10-02"], "3013", 104_000_000, 30_000_000 + 4_347_200)
        # 佳大：私募 5000 萬股都給大戶 → 總股數 1.5 億、大戶 8000 萬 → x＝33.33 → 3√x＝17.32，照算（莊爸也照列）
        rows_by_week["2026-10-02"] = edit(rows_by_week["2026-10-02"], "2033", 150_000_000, 80_000_000)
        # 益航：上週只有大戶股數＋比例（30%，回推總股數 1 億），這週總股數 8400 萬、大戶變 8000 萬 → 減少 16% 不進排行
        rows_by_week["2026-09-24"] = [r for r in rows_by_week["2026-09-24"] if not (r[0] == "2601" and r[1] == 17)]
        rows_by_week["2026-09-24"] = [(c, lv, h, sh, 7.5 if c == "2601" and lv == 12 else (22.5 if c == "2601" and lv == 15 else pct))
                                      for c, lv, h, sh, pct in rows_by_week["2026-09-24"]]
        rows_by_week["2026-10-02"] = edit(rows_by_week["2026-10-02"], "2601", 84_000_000, 80_000_000)
        for d, rows in rows_by_week.items():
            fundamentals_daily.save_tdcc(d, rows)
        with database.get_connection() as c:
            c.executemany("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, ?, 'x')",
                          [("2484", "希華", "TSE"), ("3042", "晶技", "TSE"), ("6127", "九豪", "OTC"), ("1727", "中華化", "TSE"),
                           ("3236", "千如", "OTC"), ("8182", "加高", "OTC")])

    def tearDown(self) -> None:
        self.groups_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        module._cache.update({"at": 0.0, "key": None, "radar": None})

    def test_payload(self) -> None:
        out = module.payload()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["week"], "2026-10-02")
        self.assertEqual(out["dates"], list(reversed(WEEKS[1:])))
        week = out["lists"][0]
        self.assertEqual(week["date"], "2026-10-02")
        # 買超榜：籌碼% ≥ 4 → 佳大（私募 +50%）17.32、希華 8.19、晟銘電（股本 +4%，x＝4.18）6.13、九豪 3√3.19＝5.36、千如 3√2.85＝5.06、
        # 晶技 3√2.43＝4.68；ETF 不在；益航（股份轉換、總股數 −16%）不進榜
        self.assertEqual([(r["code"], r["chip"]) for r in week["buy"]],
                         [("2033", 17.32), ("2484", 8.19), ("3013", 6.13), ("6127", 5.36), ("3236", 5.06), ("3042", 4.68)])
        self.assertEqual([r["rank"] for r in week["buy"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual(week["buy"][0]["capital"], 50.0)
        yh = module.stock("2601")
        self.assertEqual((yh["capital"], yh["excluded"]), (-16.0, True))
        star = {r["code"]: r["star"] for r in week["buy"]}
        self.assertTrue(star["6127"])                 # 9/24 也在買超榜（3√2.0＝4.24）
        self.assertFalse(star["2484"])                # 9/24 3√1.5＝3.67 不到 4
        self.assertEqual([(r["code"], r["chip"], r["star"]) for r in week["sell"]], [("1727", -7.28, True)])   # 桂田文創減資那週不進榜
        self.assertIsNone(week["buy"][1]["capital"])
        cap = next(r for r in week["buy"] if r["code"] == "3013")
        self.assertEqual((cap["chip"], cap["capital"]), (6.13, 4.0))     # 股本 +4%：照列、標出來
        trail = module.stock("4806")["trail"]
        self.assertEqual((trail[0]["chip"], trail[0]["capital"], trail[0].get("excluded")), (-40.0, -30.0, True))
        self.assertEqual(week["buy"][1]["group"], "石英")
        self.assertEqual(len(out["lists"]), 6)        # 最多 8 週，這裡只有 6 週可比

        # 族群：石英前 5 檔（8.19、4.68、3√1.11＝3.16、3√0.45＝2.01、−0.73）平均 3.46，跟莊爸的石英 +3.46% 一樣算法
        groups = {g["name"]: g for g in out["groups"]}
        self.assertEqual(groups["石英"]["avg"], 3.46)
        self.assertEqual([m["code"] for m in groups["石英"]["members"]][:2], ["2484", "3042"])
        self.assertNotIn("股期標的", groups)
        self.assertEqual(out["groups"][0]["name"], "被動元件")   # (5.36＋5.06)/2

        # 連續增：六週內五週增（晶技 6/6、希華 5/6），連三週增
        six = {r["code"]: r for r in out["streaks"]["six"]["rows"]}
        self.assertEqual((six["3042"]["ups"], six["2484"]["ups"]), (6, 5))
        self.assertEqual(out["streaks"]["six"]["from"], "2026-08-28")
        three = [r["code"] for r in out["streaks"]["three"]["rows"]]
        self.assertIn("2484", three)
        self.assertNotIn("6127", three)               # 9/18 沒增

        # 累積榜：每週前十名；九豪 8/28、9/24、10/02 三次（9/24 第 1 名）
        cum = out["cumulative"]
        self.assertEqual(cum["dates"][-1], "2026-10-02")
        row = next(r for r in cum["rows"] if r["code"] == "6127")
        self.assertEqual(sum(1 for rank, _ in row["grid"] if rank), 3)
        self.assertEqual(row["grid"][cum["dates"].index("2026-09-24")], [1, 4.24])

        # 熱門股：前十名＋前五大族群第一名
        hot = out["hot"]
        self.assertEqual([c["code"] for c in hot["cards"]][:6], ["2033", "2484", "3013", "6127", "3236", "3042"])
        self.assertEqual(hot["leaders"][0]["group"], "被動元件")
        self.assertEqual(hot["cards"][1]["trail"][0], {"date": "2026-10-02", "chip": 8.19, "rank": 2})

        # 切舊的一週
        old = module.payload("2026-09-24")
        self.assertEqual(old["week"], "2026-09-24")
        lists = {w["date"]: w for w in old["lists"]}     # 名單一次給最近 8 週（前端切週不用再抓）
        self.assertEqual([r["code"] for r in lists["2026-09-24"]["buy"]], ["6127"])
        self.assertEqual(old["cumulative"]["dates"][-1], "2026-09-24")
        with self.assertRaises(ValueError):
            module.payload("2026-01-02")

    def test_stock_and_institutional(self) -> None:
        with database.get_connection() as c:
            from chips_daily import _schema as chips_schema

            chips_schema(c)
            rows = [("2026-09-29", 4_995_000), ("2026-09-30", -1_199_000), ("2026-10-01", 5_018_000), ("2026-10-02", -775_000), ("2026-09-24", -16_000)]
            c.executemany("""INSERT INTO institutional_daily (trade_date, stock_code, market, stock_name, foreign_net, trust_net, dealer_net, total_net, source, updated_at)
                             VALUES (?, '2484', 'TSE', '希華', ?, 0, 0, ?, 'twse', 'x')""", [(d, v, v) for d, v in rows])
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES ('2484', ?, 80, 81, 79, 80, ?)",
                          [("2026-09-24T00:00:00+00:00", 37009), ("2026-09-29T00:00:00+00:00", 30000), ("2026-09-30T00:00:00+00:00", 24000),
                           ("2026-10-01T00:00:00+00:00", 40000), ("2026-10-02T00:00:00+00:00", 45845)])
        out = module.stock("2484")
        self.assertEqual((out["code"], out["name"], out["group"], out["chip"], out["rank"]), ("2484", "希華", "石英", 8.19, 2))
        self.assertEqual(len(out["trail"]), 6)
        self.assertEqual(out["groupAvg"], 3.46)
        inst = out["inst"]
        self.assertEqual(inst["days"][0], {"date": "2026-10-02", "foreign": -775, "trust": 0, "dealer": 0, "total": -775, "market": "TSE", "pct": -1.7})
        last = inst["weeks"][-1]
        self.assertEqual((last["date"], last["total"], last["days"]), ("2026-10-02", 8039, 4))   # 9/29～10/02（9/25 中秋休市）
        self.assertEqual(last["pct"], round(8039 / 139845 * 100, 1))
        with self.assertRaises(ValueError):
            module.stock("9999")

    def test_endpoints(self) -> None:
        from fastapi.testclient import TestClient

        import persistent_app

        client = TestClient(persistent_app.app)
        r = client.get("/api/hub/chip-radar")
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.json()["week"], "2026-10-02")
        self.assertEqual(client.get("/api/hub/chip-radar", params={"week": "2026-09-18"}).json()["week"], "2026-09-18")
        self.assertEqual(client.get("/api/hub/chip-radar", params={"week": "2025-01-03"}).status_code, 422)
        r = client.get("/api/hub/chip-radar/stock", params={"code": "2484"})
        self.assertEqual((r.status_code, r.json()["chip"]), (200, 8.19))
        self.assertEqual(client.get("/api/hub/chip-radar/stock", params={"code": "9999"}).status_code, 404)

    def test_empty(self) -> None:
        with database.get_connection() as c:
            c.execute("DELETE FROM tdcc_weekly")
        module._cache.update({"at": 0.0, "key": None, "radar": None})
        self.assertEqual(module.payload()["status"], "empty")


if __name__ == "__main__":
    unittest.main()
