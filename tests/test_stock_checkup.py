"""每日持股健診：分數、防守線（含紅半）、七科、建表與端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch
import brew_launch_history
import chips_daily
import database
import fundamentals_daily
import heilong_backtest
import persistent_app
import stock_checkup as module
from trading_days import previous_trading_day

GROUPS = {"矽晶圓": [("6182", "合晶"), ("6488", "環球晶"), ("5483", "中美晶")], "半導體": [("2330", "台積電")]}


def trading_dates(last: str, count: int) -> list[str]:
    out = [last]
    d = date.fromisoformat(last)
    while len(out) < count:
        d = previous_trading_day(d)
        out.append(d.isoformat())
    return list(reversed(out))


def bar(d, o, h, l, c, v=1000):
    return (d, o, h, l, c, v)


class ScoreTests(unittest.TestCase):
    def test_tech_fund_chip_overall(self) -> None:
        self.assertEqual((module.tech_score(None), module.tech_score(15), module.tech_score(12), module.tech_score(9)), (None, 100, 80, 60))
        self.assertIsNone(module.fund_score(None, None, None))
        self.assertEqual(module.fund_score(53.3, 10.1, 28.7), 86)         # 50+30+6+0
        self.assertEqual(module.fund_score(-20, -12, 60), 50 - 18 - 6 - 12)
        self.assertEqual(module.fund_score(200, 50, 5), 98)               # 上限 100 內：50+30+6+12
        self.assertIsNone(module.chip_score(None, None, None, None, 100))
        self.assertEqual(module.chip_score(2.0, 2, None, None, None), 50 + 8 + 6)
        self.assertEqual(module.chip_score(10.0, 5, 500, 200, 100), 100)                     # 各上限 50+25+9+15+10 → 封頂 100
        self.assertEqual(module.chip_score(-3.0, 0, -100, None, 100), 50 - 12 - 15)          # 法人賣超佔 20% → −15 上限
        self.assertEqual(module.overall_score({"fund": 87, "chip": 58, "tech": 100}), 82)
        self.assertEqual(module.overall_score({"fund": None, "chip": 60, "tech": 100}), 80)
        self.assertEqual(module.overall_score({"fund": None, "chip": 60, "tech": 100}, zero_missing=True), 53)
        self.assertEqual(module.overall_score({"fund": 80, "chip": 40, "tech": 60}, {"fund": 50, "chip": 0, "tech": 50}), 70)
        self.assertIsNone(module.overall_score({"fund": None, "chip": None, "tech": None}))
        self.assertEqual([module.overall_label(v) for v in (82, 67, 54, 35, 10, None)], ["強", "中上", "普通", "偏弱", "弱", None])

    def test_half_line(self) -> None:
        window = [bar("d1", 100, 104, 98, 103), bar("d2", 103, 110, 102, 109), bar("d3", 109, 112, 106, 111)]   # 高 112 低 98 → 中點 105，振幅 14.3%
        self.assertEqual(module.half_line(window, 111), 105.0)
        self.assertIsNone(module.half_line(window, 105.5))          # 收盤沒站在中點之上 1%
        self.assertIsNone(module.half_line(window, 100))
        flat = [bar("d1", 100, 103, 99, 102), bar("d2", 102, 104, 100, 103), bar("d3", 103, 105, 101, 104)]   # 振幅 6%
        self.assertIsNone(module.half_line(flat, 104))
        self.assertIsNone(module.half_line([], 100))

    def test_defense_lines(self) -> None:
        dates = trading_dates("2026-09-24", 25)
        bars = [bar(d, 100, 101, 99, 100) for d in dates[:-4]]
        bars += [bar(dates[-4], 100, 101, 97, 98), bar(dates[-3], 98, 106, 95, 105), bar(dates[-2], 105, 112, 104, 110), bar(dates[-1], 110, 113, 108, 111)]
        d = module.defense_lines(bars)
        self.assertEqual((d["low3Y"], d["low3T"]), (95, 95))                        # 昨日值＝前三天最低；明日值＝含今天近三日最低
        self.assertEqual(d["ma20Y"], round((100 * 17 + 98 + 105 + 110) / 20, 2))
        self.assertEqual(d["ma20T"], round((100 * 16 + 98 + 105 + 110 + 111) / 20, 2))
        self.assertEqual(d["halfY"], 103.5)                                          # 昨天看：高 112 低 95 → 103.5，昨收 110 站上
        self.assertEqual(d["halfT"], 104.0)                                          # 今天看：高 113 低 95 → 104
        self.assertEqual((d["brkMa"], d["brkL3"], d["brkHalf"], d["nearMa"], d["label"]), (False, False, False, False, "✅ 守住"))
        # 今收跌破昨日紅半但守住月線與三日低
        d2 = module.defense_lines(bars[:-1] + [bar(dates[-1], 110, 111, 102, 103)])   # 103 < 昨日紅半 103.5，但在月線 101.65 與三日低 95 之上
        self.assertEqual((d2["brkHalf"], d2["brkMa"], d2["label"]), (True, False, "🔻 破紅半"))
        # 破月線＋距月線 10% 以內 → 即將穿惡；再破三日低 → 雙破
        d3 = module.defense_lines(bars[:-1] + [bar(dates[-1], 110, 111, 96, 97)])
        self.assertEqual((d3["brkMa"], d3["brkL3"], d3["nearMa"], d3["label"]), (True, False, True, "🌙 破月線・🐉 即將穿惡"))
        d4 = module.defense_lines(bars[:-1] + [bar(dates[-1], 95, 96, 90, 91)])
        self.assertEqual(d4["label"], "💥 雙破")
        bars5 = [bar(d_, 100, 101, 99, 100) for d_ in dates[:-4]] + [bar(dates[-4], 100, 105, 103, 104), bar(dates[-3], 104, 108, 104, 107), bar(dates[-2], 107, 110, 106, 109), bar(dates[-1], 109, 110, 101, 102)]
        d5 = module.defense_lines(bars5)                                             # 三日低 103 破了、月線 101 守住、三日振幅不到 10% 沒有紅半
        self.assertEqual((d5["low3Y"], d5["ma20Y"], d5["halfY"], d5["label"]), (103, 101.0, None, "📉 破三日低"))
        self.assertIsNone(module.defense_lines(bars[:20]))

    def test_subjects(self) -> None:
        s = module.subjects(9, None, None, None, 0.73, 28.7, 53.3, 8230.0, -5713.0)
        self.assertEqual(s["sc"], {"ma": 1, "grp": None, "pos": None, "chip": 1, "pe": 1, "rev": 2, "inst": 1})
        self.assertEqual((s["red"], s["green"], s["total"], s["possible"], s["cls"]), (1, 0, 6, 10, "中等"))
        s2 = module.subjects(15, 12.0, 1, 9, 5.0, 15.0, 40.0, 100.0, 10.0)
        self.assertEqual((s2["red"], s2["green"], s2["cls"]), (7, 0, "強勢"))
        s3 = module.subjects(3, 5.0, 9, 9, -2.0, 60.0, -10.0, -50.0, -5.0)
        self.assertEqual((s3["red"], s3["green"], s3["cls"]), (0, 6, "弱勢"))
        self.assertEqual(module.subjects(None, None, None, None, None, None, None, None, None)["cls"], None)
        self.assertEqual(module.subjects(None, 12.0, 1, 5, None, None, None, None, None)["cls"], None)      # 只有兩科有資料不分級
        self.assertEqual(module.subjects(None, 12.0, 1, 5, 5.0, None, None, None, None)["cls"], "強勢")

    def test_normalize_codes(self) -> None:
        self.assertEqual(module.normalize_codes("2481/2408, 2344 2330\n2409，6182、2481"), ["2481", "2408", "2344", "2330", "2409", "6182"])
        self.assertEqual(module.normalize_codes(["2330", "00981a"]), ["2330", "00981A"])
        self.assertEqual(module.normalize_codes("台積電"), [])
        self.assertEqual(len(module.normalize_codes(" ".join(str(1000 + i) for i in range(300)))), module.MAX_CODES)


class RebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.patches = [patch.object(m, "STOCK_GROUPS", GROUPS) for m in (module, brew_launch, brew_launch_history, heilong_backtest)]
        for p in self.patches:
            p.start()
        brew_launch_history._group_by_code.clear()
        self.dates = trading_dates("2026-09-24", 243)
        d = self.dates
        rows = []
        # 合晶：平盤 240 天 → 拉三天（黑K收在高檔）
        rows += [("6182", x + "T00:00:00", 100, 101, 99, 100, 500) for x in d[:240]]
        rows += [("6182", d[240] + "T00:00:00", 100, 106, 99, 105, 1500), ("6182", d[241] + "T00:00:00", 106, 114, 105, 113, 2000), ("6182", d[242] + "T00:00:00", 114, 116, 110, 112, 1800)]
        # 環球晶：只有 30 天，算不出均線分數但有防守線
        rows += [("6488", x + "T00:00:00", 500, 505, 495, 500, 300) for x in d[-30:]]
        # 中美晶：資料停在前一天（stale）
        rows += [("5483", x + "T00:00:00", 200, 202, 198, 200, 300) for x in d[-30:-1]]
        # 台積電：平盤
        rows += [("2330", x + "T00:00:00", 1000, 1001, 999, 1000, 20000) for x in d]
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            c.execute("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES ('6488', '環球晶X', 'OTC', 'x')")
        fundamentals_daily.save_tdcc("2026-09-11", [("6182", 12, 10, 1_000_000, 10.0)])
        fundamentals_daily.save_tdcc("2026-09-18", [("6182", 12, 10, 1_100_000, 11.0)])
        fundamentals_daily.save_pe("2026-09-24", "OTC", [{"code": "6182", "pe": 22.0, "pbr": 2.0, "yield": 1.0}], "test")
        fundamentals_daily.save_revenue("OTC", [{"code": "6182", "ym": "2026-08", "revenue": 1000, "yoy": 35.0, "mom": 5.0}])
        for x in d[-5:]:
            chips_daily.save_institutional(x, "OTC", [{"code": "6182", "name": "合晶", "foreign": 1_000_000, "trust": 0, "dealer": 0, "total": 1_000_000}], "test")

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        for p in self.patches:
            p.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_rebuild_and_checkup(self) -> None:
        d = self.dates
        result = module.rebuild()
        self.assertEqual((result["date"], result["rows"], result["stocks"]), (d[-1], 4, 4))
        r = module.checkup("6182/6488, 5483 2330 9999")
        self.assertEqual((r["status"], r["date"], r["missing"]), ("ok", d[-1], ["9999"]))
        rows = {x["code"]: x for x in r["rows"]}
        self.assertEqual([x["code"] for x in r["rows"]], ["6182", "6488", "5483", "2330"])
        hj = rows["6182"]
        self.assertEqual((hj["name"], hj["group"], hj["stale"], hj["close"], hj["chgPct"]), ("合晶", "矽晶圓", False, 112.0, -0.88))
        self.assertEqual((hj["score"], hj["score2"], hj["scores"]["tech"]), (15, 15, 100))
        self.assertEqual(hj["chg20Pct"], 12.0)
        self.assertEqual(hj["fund"], {"yoy": 35.0, "mom": 5.0, "ym": "2026-08", "revenue": 1000, "pe": 22.0, "peDate": "2026-09-24"})
        self.assertEqual(hj["scores"]["fund"], 50 + 22 + 2 + 3)
        self.assertEqual((hj["chip"]["weekPct"], hj["chip"]["weeks"], hj["chip"]["inst5"], hj["chip"]["instToday"], hj["chip"]["instStreak"], hj["chip"]["mf5"]), (10.0, 1, 5000.0, 1000.0, 5, None))
        self.assertEqual(hj["chip"]["avgVol5"], (500 + 500 + 1500 + 2000 + 1800) / 5)
        self.assertEqual(hj["scores"]["chip"], 50 + 25 + 3 + 15)      # 週增 10%×4 上限 25、連 1 週 +3、法人 5000 張 / 5 日量 6300 張 → 上限 15
        self.assertEqual(hj["scores"]["overall"], round((77 * 34 + 93 * 33 + 100 * 33) / 100))
        self.assertEqual(hj["scores"]["overallLabel"], "強")
        df = hj["def"]
        self.assertEqual((df["low3Y"], df["low3T"], df["halfY"], df["halfT"], df["label"]), (99, 99, 106.5, 107.5, "✅ 守住"))   # 昨天看：高 114 低 99 → 106.5；今天看：高 116 低 99 → 107.5
        self.assertEqual((hj["groupAvg2"], hj["groupRank"], hj["groupN"]), (15.0, 2, 2))   # 族內只有合晶與環球晶有今天的日K；合晶 −0.88% 排第 2
        self.assertEqual(hj["subjects"]["sc"], {"ma": 2, "grp": 2, "pos": 1, "chip": 2, "pe": 1, "rev": 2, "inst": 2})
        self.assertEqual((hj["subjects"]["red"], hj["subjects"]["green"], hj["subjects"]["cls"]), (5, 0, "強勢"))
        self.assertEqual(hj["hist"]["chip8"], [["2026-09-18", 10.0]])
        self.assertEqual(len(hj["hist"]["score10"]), 10)
        self.assertEqual(hj["hist"]["score10"][-1], [d[-1], 15])
        self.assertEqual(hj["hist"]["inst10"][-1], [d[-1], 1_000_000])
        gw = rows["6488"]
        self.assertEqual((gw["name"], gw["score2"], gw["scores"]["tech"], gw["scores"]["fund"], gw["scores"]["chip"]), ("環球晶", None, None, None, None))
        self.assertIsNone(gw["scores"]["overall"])
        self.assertEqual((gw["def"]["ma20Y"], gw["def"]["label"]), (500.0, "✅ 守住"))
        self.assertEqual((gw["groupRank"], gw["groupN"]), (1, 2))
        self.assertTrue(rows["5483"]["stale"])
        self.assertEqual(rows["5483"]["date"], d[-2])
        self.assertIsNone(rows["5483"]["groupRank"])
        self.assertEqual((rows["2330"]["group"], rows["2330"]["score2"], rows["2330"]["subjects"]["cls"]), ("半導體", 6, None))   # 平盤：官網式 6（同值算新高）；只有兩科有資料不分級
        self.assertEqual(module.collector_status()["lastDate"], d[-1])
        self.assertEqual((hj["cross"]["prevBelow"], hj["cross"]["up"]), (False, False))       # 昨收 113 在月線上，不算穿惡
        self.assertEqual((hj["groupSrank"], hj["groupStrength"]), (1, 12.0))                  # 矽晶圓：合晶 12 分（2+2+1+2+1+2+2；環球晶沒有七科）
        self.assertIsNone(rows["2330"]["groupSrank"])                                       # 半導體沒有分級的成員，不進強度榜
        self.assertEqual((result["topGroups"], result["todayList"], result["cross"]), (1, 1, 0))
        again = module.rebuild()
        self.assertEqual(again["rows"], 4)

    def test_cross_up_and_diag(self) -> None:
        d = self.dates
        # 環球晶改成：昨收在月線下、今天站上月線 3% 且量夠 → 穿惡
        with database.get_connection() as c:
            c.execute("DELETE FROM bars_1d WHERE stock_code = '6488'")
            rows = [("6488", x + "T00:00:00", 500, 505, 495, 500, 300) for x in d[-30:-2]]
            rows += [("6488", d[-2] + "T00:00:00", 480, 482, 470, 475, 300), ("6488", d[-1] + "T00:00:00", 480, 520, 478, 516, 900)]
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        result = module.rebuild()
        self.assertEqual(result["cross"], 1)
        r = module.diag("6488")
        self.assertEqual((r["status"], r["code"], r["stock"]["name"], r["stock"]["cross"]["up"], r["stock"]["cross"]["prevBelow"]), ("ok", "6488", "環球晶", True, True))
        self.assertGreater(r["stock"]["cross"]["dist"], 2.0)
        self.assertEqual(len(r["bars"]), 30)
        self.assertEqual(r["bars"][-1][:5], [d[-1], 480.0, 520.0, 478.0, 516.0])
        self.assertEqual([x["code"] for x in r["siblings"]], ["6182", "6488", "5483"])       # 同族依七科總分排；中美晶資料舊的沒有七科排最後
        self.assertEqual(r["groupInfo"]["g"], "矽晶圓")
        self.assertEqual([g["g"] for g in r["top"]["groups"]], ["矽晶圓"])
        self.assertEqual([x["code"] for x in r["top"]["list"]], ["6182"])                     # 只有強勢／中等進今日名單
        cross = r["cross"]
        self.assertEqual((cross["n"], [x["code"] for x in cross["groups"]["中等"]] + [x["code"] for x in cross["groups"]["弱勢"]]), (1, ["6488"]))
        self.assertEqual(module.diag("9999")["status"], "missing")
        self.assertEqual(module.diag("")["status"], "empty")
        client = TestClient(persistent_app.app)
        self.assertEqual(client.get("/api/hub/diag", params={"code": "6182"}).json()["stock"]["code"], "6182")

    def test_endpoints(self) -> None:
        module.rebuild()
        client = TestClient(persistent_app.app)
        r = client.get("/api/hub/checkup", params={"codes": "6182,2330"}).json()
        self.assertEqual((r["status"], [x["code"] for x in r["rows"]], r["missing"]), ("ok", ["6182", "2330"], []))
        self.assertEqual(client.get("/api/hub/checkup").json()["rows"], [])
        self.assertEqual(client.get("/api/hub/checkup/status").json()["lastDate"], self.dates[-1])
        self.assertEqual(client.post("/api/hub/checkup/rebuild").json()["result"]["rows"], 4)

    def test_empty(self) -> None:
        self.assertEqual(module.checkup("2330")["status"], "empty")


if __name__ == "__main__":
    unittest.main()
