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
import swing_report
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
        # subjects(均線, 族群名次, 族內近5日平均名次, 族內檔數, 籌碼%, PE, 族群平均PE, 營收年增, 法人5日, 法人今日)
        s = module.subjects(9, None, None, None, 0.73, 28.7, 30.0, 53.3, 8230.0, -5713.0)
        self.assertEqual(s["sc"], {"ma": 1, "grp": None, "pos": None, "chip": 1, "pe": 1, "rev": 2, "inst": 1})
        self.assertEqual((s["red"], s["green"], s["total"], s["possible"], s["cls"]), (2, 0, 6, 10, "中等"))
        s2 = module.subjects(15, 1, 1.0, 9, 5.0, 15.0, 40.0, 100.0, 10.0, 10.0)
        self.assertEqual((s2["red"], s2["green"], s2["cls"]), (7, 0, "強勢"))          # 本益比 1 分也算紅
        s3 = module.subjects(3, 59, 8.0, 9, -2.0, 60.0, 30.0, -10.0, -50.0, -5.0)
        self.assertEqual((s3["red"], s3["green"], s3["cls"]), (0, 7, "弱勢"))
        self.assertEqual(module.subjects(None, None, None, None, None, None, None, None, None, None)["cls"], None)
        self.assertEqual(module.subjects(None, 1, 1.0, 5, None, None, None, None, None, None)["cls"], None)      # 只有兩科有資料不分級
        self.assertEqual(module.subjects(None, 1, 1.0, 5, 5.0, None, None, None, None, None)["cls"], "強勢")

    def test_subjects_zhuang_1029(self) -> None:
        # 老師 10/29 問診 API（/api/diag/q）的七科：十銓 ma2 grp1 pos2 chip1 pe1 rev2 inst2＝強勢（紅 5 綠 0）；創見 2,1,1,1,1,2,2 共 10/14 只是中等（要超過七成五）
        sw = module.subjects(12, 13, 5.6, 17, 1.27, 4.4, 13.3, 109.7, 4134.0, 1245.0)
        self.assertEqual(sw["sc"], {"ma": 2, "grp": 1, "pos": 2, "chip": 1, "pe": 1, "rev": 2, "inst": 2})
        self.assertEqual((sw["red"], sw["green"], sw["total"], sw["cls"]), (5, 0, 11, "強勢"))
        self.assertEqual(module.subjects(11, 13, 6.4, 17, 1.0, 5.0, 13.3, 80.0, 100.0, 10.0)["cls"], "中等")
        # 邊界：均線 10 得 2、9 得 1、6 得 1、5 得 0；籌碼 2.0 得 2、-1.0 得 1；本益比超過 150 沒分、高於族群平均 0 分；名次前 12／25
        self.assertEqual(module.subjects(10, 12, None, None, 2.0, None, None, None, None, None)["sc"]["ma"], 2)
        self.assertEqual([module.subjects(x, None, None, None, None, None, None, None, None, None)["sc"]["ma"] for x in (9, 6, 5)], [1, 1, 0])
        self.assertEqual([module.subjects(None, None, None, None, c, None, None, None, None, None)["sc"]["chip"] for c in (2.0, 1.99, -1.0, -1.04)], [2, 1, 1, 0])
        self.assertEqual([module.subjects(None, None, None, None, None, pe, 40.0, None, None, None)["sc"]["pe"] for pe in (40.0, 41.0, 160.0, -3.0)], [1, 0, None, None])
        self.assertEqual([module.subjects(None, r, None, None, None, None, None, None, None, None)["sc"]["grp"] for r in (12, 13, 25, 26)], [2, 1, 1, 0])
        # 強勢要超過七成五：6 科 10／12 強勢、9／12 中等；7 科 11／14 強勢、10／14 中等；弱勢是 4 成 2 以下（7 科 5／14 弱勢、6／14 中等）
        def mk(*scores: int) -> str | None:   # 直接用各科分數湊：均線、族群、族內、籌碼、營收、法人（本益比不給＝6 科）
            ma, grp, pos, chip, rev, inst = scores
            return module.subjects(ma, grp, 1.0 if pos == 2 else 5.0 if pos == 1 else 9.0, 9, chip, None, None, rev, inst, 1.0)["cls"]

        self.assertEqual(mk(10, 1, 2, 2, 2, 2), "強勢")      # 6 科 12 分的上限是 12／12；先確認最高分是強勢
        self.assertEqual(mk(10, 1, 1, 1, 2, 2), "中等")      # 6 科拿 9 分（每科 2、2、2、2、1、0 這種組合都 ≤9）還是中等

    def test_subjects_zhuang_1022(self) -> None:
        # 老師 10/22 完整問診：華通（低軌衛星）弱勢 🔴1·🟢3、國巨（被動元件）弱勢 🔴2·🟢4（2026-10-29 改用老師 API 的門檻後，兩檔還是弱勢）
        hua = module.subjects(4, 59, 3.6, 7, -0.07, 36.2, 37.9, 15.0, -3971.0, -5585.0)
        self.assertEqual(hua["sc"], {"ma": 0, "grp": 0, "pos": 1, "chip": 1, "pe": 1, "rev": 1, "inst": 0})   # 趨勢偏弱、後段沒資金、中間位置、沒明顯進出、低於族均、溫和成長、籌碼被倒
        self.assertEqual(hua["cls"], "弱勢")
        kuo = module.subjects(3, 65, 14.0, 22, -1.79, 34.1, 40.0, 46.1, -29225.0, -16585.0)
        self.assertEqual(kuo["sc"], {"ma": 0, "grp": 0, "pos": 1, "chip": 0, "pe": 1, "rev": 2, "inst": 0})   # 主力在退、成長很強
        self.assertEqual((kuo["red"], kuo["green"], kuo["cls"]), (2, 4, "弱勢"))

    def test_group_helpers(self) -> None:
        # 族群本益比平均：老師低軌衛星 37.9＝耀登 35.1、兆赫 43.1、萊德 63.8、華通 36.2、啟碁 25.0、同欣電 24.2（燿華沒有本益比）六檔的平均
        members = {"低軌衛星": ["3138", "2367", "2485", "7717", "2313", "6285", "6271"], "空運": ["2610", "2618"]}
        pes = {"3138": 35.1, "2367": None, "2485": 43.1, "7717": 63.8, "2313": 36.2, "6285": 25.0, "6271": 24.2, "2610": 9.4, "2618": 600.0}
        means = module.group_pe_means(pes, members)
        self.assertEqual(round(means["低軌衛星"], 1), 37.9)
        self.assertEqual(means["空運"], 9.4)                       # 超過 150 的異常值不算
        # 族群名次：成員平均漲幅排名
        ranks = module.group_day_ranks({"3138": 1.0, "2313": -1.0, "2610": 2.0, "2618": 2.0, "9999": 5.0}, members)
        self.assertEqual(ranks, {"空運": 1, "低軌衛星": 2})
        # 族內近 5 日平均名次：3 檔 × 5 天，A 每天第 1、B 第 2、C 第 3；只有最近 5 天算，更早的不算；基準日沒日K的不排
        days = ["d1", "d2", "d3", "d4", "d5", "d6", "d7"]
        daily = {"A": {d: 3.0 for d in days}, "B": {d: 2.0 for d in days}, "C": {d: 1.0 for d in days}}
        daily["C"]["d1"] = 9.0                                     # 更早那天 C 第 1，不在最近 5 天內
        daily["D"] = {d: 0.0 for d in days[:-1]}                   # D 基準日（d7）沒有日K
        pos = module.pos_averages(daily, {"g": ["A", "B", "C", "D"]}, "d7")
        self.assertEqual((pos[("g", "A")], pos[("g", "B")], pos[("g", "C")]), ((1.0, 3), (2.0, 3), (3.0, 3)))
        self.assertNotIn(("g", "D"), pos)
        changes = module.daily_changes([("d1", 100.0), ("d2", 110.0), ("d3", 99.0)])
        self.assertEqual(list(changes), ["d2", "d3"])
        self.assertAlmostEqual(changes["d2"], 10.0)
        self.assertAlmostEqual(changes["d3"], -10.0)

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
        # 籌碼科用籌碼雷達的籌碼%（老師問診名單的「籌碼」欄）：合晶這週 +4.5%（≥3 得 2 分）
        self.patches.append(patch.object(swing_report, "radar_week", return_value={"week": "2026-09-18", "stocks": {"6182": {"chip": 4.5, "chip2": 4.5}}}))
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
        self.assertEqual((hj["margin"]["factor"], hj["margin"]["monthHigh"], rows["6488"]["margin"]["factor"]), (0.78, 116.0, 0.65))   # 融資斷頭線：上市 0.78、上櫃（環球晶）0.65
        self.assertEqual(hj["chip"]["radar"], 4.5)           # 籌碼雷達的籌碼%；大戶週增 weekPct 另外留著（三面向分數用）
        self.assertIsNone(rows["6488"]["chip"]["radar"])     # 雷達沒有這檔：籌碼科不計分，不拿大戶週增 % 來湊
        self.assertEqual((hj["subjects"]["red"], hj["subjects"]["green"], hj["subjects"]["cls"]), (6, 0, "強勢"))
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
        self.assertEqual((rows["2330"]["group"], rows["2330"]["score2"], rows["2330"]["subjects"]["cls"]), ("半導體", 6, None))   # 平盤：官網式 6（站上均線平手算站上 6、創新高平手不算 0、排列 0）；只有兩科有資料不分級
        self.assertEqual(module.collector_status()["lastDate"], d[-1])
        self.assertEqual((hj["cross"]["prevBelow"], hj["cross"]["up"]), (False, False))       # 昨收 113 在月線上，不算穿惡
        self.assertEqual((hj["groupSrank"], hj["groupStrength"]), (1, 12.0))                  # 矽晶圓：合晶 12 分（2+2+1+2+1+2+2，本益比最高 1 分；環球晶沒有七科）
        self.assertIsNone(rows["2330"]["groupSrank"])                                       # 半導體沒有分級的成員，不進強度榜
        self.assertEqual((result["topGroups"], result["todayList"], result["cross"]), (1, 1, 0))
        again = module.rebuild()
        self.assertEqual(again["rows"], 4)

    def test_checkup_for_a_stored_past_date(self) -> None:
        """2026-10-13 使用者：形態觀察股要看昨天／前天——健診可以指定日期（表裡有存的才有）。"""
        d = self.dates
        module.rebuild()
        r = module.checkup("6182", d[-1])
        self.assertEqual((r["status"], r["date"], r["dates"], [x["code"] for x in r["rows"]]), ("ok", d[-1], [d[-1]], ["6182"]))
        missing = module.checkup("6182", d[-2])
        self.assertEqual(missing["status"], "empty")
        self.assertIn("只留最近", missing["reason"])
        from fastapi.testclient import TestClient
        import persistent_app
        client = TestClient(persistent_app.app)
        self.assertEqual(client.get("/api/hub/checkup", params={"codes": "6182", "date": d[-1]}).json()["date"], d[-1])
        self.assertEqual(client.get("/api/hub/checkup", params={"codes": "6182", "date": "bad"}).status_code, 422)

    def test_checkup_carries_hi_len_from_picker_table(self) -> None:
        """創高天數借創高黑選股的特徵表（2026-10-05 使用者：自選股盤後籌碼要看）。"""
        d = self.dates
        module.rebuild()
        self.assertIsNone(module.checkup("6182")["rows"][0]["hiLen"])            # 特徵表還沒資料：不給
        insert = "INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, hi_len) VALUES (?, ?, 1, 1, 1, 1, 1, ?)"
        with database.get_connection() as c:
            heilong_backtest._schema(c)
            c.executemany(insert, [(d[-2], "6182", 5), (d[-2], "2330", 1)])
        rows = {x["code"]: x["hiLen"] for x in module.checkup("6182 2330 6488")["rows"]}
        self.assertEqual(rows, {"6182": 5, "2330": 1, "6488": None})              # 特徵表晚一天建好：先用前一天的
        with database.get_connection() as c:
            c.execute(insert, (d[-1], "6182", 242))
        rows = {x["code"]: x["hiLen"] for x in module.checkup("6182 2330")["rows"]}
        self.assertEqual(rows, {"6182": 242, "2330": None})                      # 用最新一天；那天沒有的不拿舊的

    def test_cross_info_threshold(self) -> None:
        # 老師 10/22 今日穿惡：麗臺 96.20 ÷ 昨天月線 88.41 = +8.8% 列；百達-KY 96.3 ÷ 94.13 = +2.3% 不列（用昨天的月線算，門檻 3%）
        d = {"ma20Y": 88.41, "ma20T": 88.62}
        c = module.cross_info(87.5, 96.2, 10337, d)
        self.assertEqual((c["prevBelow"], c["up"], round(c["dist"], 1)), (True, True, 8.8))
        c = module.cross_info(90.1, 96.3, 1312, {"ma20Y": 94.13, "ma20T": 93.5})
        self.assertEqual((c["prevBelow"], c["up"], round(c["dist"], 1)), (True, False, 2.3))
        self.assertFalse(module.cross_info(87.5, 96.2, 300, d)["up"])        # 量不到 500 張
        self.assertFalse(module.cross_info(89.0, 96.2, 5000, d)["up"])       # 昨收已在月線上，不算穿
        self.assertIsNone(module.cross_info(None, 96.2, 5000, d))
        self.assertIsNone(module.cross_info(87.5, 96.2, 5000, None))

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
        import margin_balance

        margin_balance.save_margin(d[-1], "OTC", [{"code": "6488", "buy": 576, "sell": 670, "repay": 19, "prev": 13071, "balance": 12958, "limit": 132028, "short": 185}], "mirror")
        r = module.diag("6488")
        self.assertEqual((r["status"], r["code"], r["stock"]["name"], r["stock"]["cross"]["up"], r["stock"]["cross"]["prevBelow"]), ("ok", "6488", "環球晶", True, True))
        self.assertEqual((r["stock"]["marginBal"]["balance"], r["stock"]["marginBal"]["chg1"], r["stock"]["marginBal"]["util"]), (12958, -113, 9.81))   # 融資水位問診當下才查
        self.assertGreater(r["stock"]["cross"]["dist"], 3.0)
        self.assertEqual(len(r["bars"]), 30)
        self.assertEqual(r["bars"][-1][:5], [d[-1], 480.0, 520.0, 478.0, 516.0])
        self.assertEqual([x["code"] for x in r["siblings"]], ["6182", "6488", "5483"])       # 同族依七科總分排；中美晶資料舊的沒有七科排最後
        self.assertIn("pull", r["stock"]["margin"])
        self.assertIn("stab", r["stock"]["margin"])                                            # 問診單檔的斷頭線也帶止跌特徵（近似落底用）
        sib = {x["code"]: x for x in r["siblings"]}
        self.assertEqual((sib["6488"]["margin"]["factor"], sorted(sib["6488"]["margin"])), (0.65, ["a", "aBroken", "aGap", "aNew", "b", "bBroken", "bGap", "bNew", "factor"]))   # 同族對照每檔帶斷頭價 A／B
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
