"""處置監獄：證交所／櫃買公告解析、第六條累積（連 3 日第一款等）與處置後重算、明天門檻、出獄表與頁面各段。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import database
import disposition_jail as module
import fundamentals_daily

TW = module.TW_TZ

TWSE_NOTICE = {
    "stat": "OK",
    "fields": ["編號", "證券代號", "證券名稱", "累計次數", "注意交易資訊", "日期", "收盤價", "本益比"],
    "data": [
        [27, "2492", "華新科", "3", "最近六個營業日累積收盤價漲幅達35.35%。且六個營業日起迄兩個營業日收盤價價差達91.00 元﹝第一款﹞。"
         "且10月08日之週轉率為13.33%﹝第四款﹞。", "115.10.08", "420.00", "47.19"],
        [27, "2492", "華新科", "3", "最近六個營業日累積收盤價漲幅達36.39%﹝第一款﹞。", "115.10.07", "428.00", "48.09"],
        [9, "1459", "聯發", "2", "最近六個營業日平均成交量較最近六十個營業日之日平均成交量放大為5.19倍<font color='#FF0000'>﹝第九款﹞</font>。",
         "115.10.07", "18.25", "7.07"],
        [1, "033569", "神基統一64購01", "3", "最近六個營業日累積收盤價跌幅達36.72%﹝第一款﹞。", "115.10.07", "0.20", "-----"],
    ],
}
TPEX_ATTENTION = {"tables": [{
    "fields": ["編號", "證券代號", "證券名稱", "累計", "注意交易資訊", "公告日期", "收盤價", "本益比", "link"],
    "data": [
        [1, "3236", "千如", 29, "最近六個營業日(含當日)累積之最後成交價漲幅達30.08%(第一款)<br>當日週轉率達18.4%(第四款)", "115/10/08",
         "58.90", "81.81", "../x"],
        [1, "3441", "聯一光", 29, "當日沖銷成交量占總成交量達71.45%（第十三款)", "115/10/08", "249.50", "102.67", "../x"],
    ],
}]}
TWSE_PUNISH = {
    "stat": "OK",
    "fields": ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置條件", "處置起迄時間", "處置措施", "處置內容", "備註"],
    "data": [[21, "115/10/07", "6213", "聯茂", 1, "連續三次及當日沖銷標準", "115/10/08～115/10/19", "第一次處置",
              "２處置期間：自民國一百十五年十月八日起至一百十五年十月十九日﹝七個營業日，如遇...〕。\n３處置措施：\n"
              "ａ以人工管制之撮合終端機執行撮合作業（約每二分鐘撮合一次）。", ""]],
}
TPEX_DISPOSAL = {"tables": [{
    "fields": ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置起訖時間", "處置原因", "處置措施", "處置內容", "收盤價", "本益比", " "],
    "data": [
        [1, "115/10/08", "6708", "天擎(../../mainboard/listed/company-detail.html?code=6708)", 2, "115/10/12~115/10/16",
         "連續3個營業日", "再次處置", "...爰自115年10月12日起5個營業日(115年10月12日至115年10月16日)改以人工管制之撮合終端機執行撮合作業(約每2分鐘撮合一次)",
         "69.20", "N/A", ""],
        [1, "115/10/05", "6708", "天擎(../../x.html?code=6708)", 2, "115/10/06~115/10/13", "連續3個營業日", "第一次處置",
         "...爰自115年10月6日起5個營業日(...)(約每2分鐘撮合一次)", "60.00", "N/A", ""],
        [1, "115/10/07", "47602", "勤凱二(../x.html)", 7, "115/10/08~115/10/15", "轉換公司債", "第一次處置", "...", "149.60", "N/A", ""],
    ],
}]}


class ParseTests(unittest.TestCase):
    def test_numbers_and_dates(self) -> None:
        self.assertEqual([module.cn_number(x) for x in ("五", "七", "十", "十三", "二十", "2")], [5, 7, 10, 13, 20, 2])
        self.assertEqual(module.roc_to_iso("115/10/08"), "2026-10-08")
        self.assertEqual(module.roc_to_iso("115.10.07"), "2026-10-07")
        self.assertEqual(module.parse_period("115/10/08～115/10/19"), ("2026-10-08", "2026-10-19"))
        self.assertEqual(module.parse_period("115/10/12~115/10/16"), ("2026-10-12", "2026-10-16"))

    def test_notices_keep_only_stocks_and_read_clauses(self) -> None:
        rows = module.parse_notice_payload(TWSE_NOTICE, "TSE")
        self.assertEqual([(r["code"], r["date"], r["clauses"]) for r in rows],
                         [("2492", "2026-10-08", [1, 4]), ("2492", "2026-10-07", [1]), ("1459", "2026-10-07", [9])])
        otc = module.parse_notice_payload(TPEX_ATTENTION, "OTC")
        self.assertEqual([(r["code"], r["clauses"]) for r in otc], [("3236", [1, 4]), ("3441", [13])])
        self.assertNotIn("<br>", otc[0]["info"])

    def test_punish_days_minutes_and_names(self) -> None:
        tse = module.parse_punish_payload(TWSE_PUNISH, "TSE")[0]
        self.assertEqual((tse["code"], tse["start"], tse["end"], tse["days"], tse["minutes"], tse["measure"]),
                         ("6213", "2026-10-08", "2026-10-19", 7, 2, "第一次處置"))
        otc = module.parse_punish_payload(TPEX_DISPOSAL, "OTC")
        self.assertEqual([(r["code"], r["name"], r["start"], r["days"], r["minutes"]) for r in otc],
                         [("6708", "天擎", "2026-10-12", 5, 2), ("6708", "天擎", "2026-10-06", 5, 2)])   # 轉換公司債 47602 不收


class AccumulationTests(unittest.TestCase):
    def test_paths_and_reset_after_disposition(self) -> None:
        days = {"2026-10-08": [1], "2026-10-07": [1, 2], "2026-10-06": [1], "2026-10-05": [1, 4], "2026-10-02": [1]}
        acc = module.accumulation(days, "2026-10-08", None)
        self.assertEqual((acc["c1"], acc["cany"], acc["n9"]), (5, 5, 5))
        self.assertTrue(acc["pathC"])          # 10 日內已 5 次，明天再 1 次就 6 次
        self.assertFalse(acc["pathA"])
        reset = module.accumulation(days, "2026-10-08", "2026-10-06")   # 10/06 公告處置：之前的不算
        self.assertEqual((reset["c1"], reset["cany"]), (2, 2))
        self.assertTrue(reset["pathA"])
        only9 = module.accumulation({"2026-10-08": [9], "2026-10-07": [13]}, "2026-10-08", None)
        self.assertEqual((only9["cany"], only9["n9"]), (0, 0))   # 第九款以後不算累積


class DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._cache["key"] = None

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def add_bars(self, code: str, closes: list[float], end: str = "2026-10-08", volume: int = 5000) -> None:
        days = list(reversed(module.trading_days_back(end, len(closes))))
        with database.get_connection() as connection:
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(code, d, c, c, c, c, volume) for d, c in zip(days, closes)])


class ThresholdTests(DbTestCase):
    WALSIN = [300.0, 310.0, 329.0, 348.74, 366.18, 380.82, 401.88, 420.0]

    def setUp(self) -> None:
        super().setUp()
        self.add_bars("2492", self.WALSIN, volume=64554)

    def test_twse_first_clause_two_paths(self) -> None:
        """上市第一款：明天 6 日累積（每天漲跌幅相加）> 32%，或 > 25% 且起迄價差 ≥ 50 元；兩條門檻都列，起點＝今天往前 4 個交易日。"""
        c = self.WALSIN
        s5 = sum((c[-k] / c[-k - 1] - 1) * 100 for k in range(1, 6))
        th = module.thresholds("2492", "TSE", "2026-10-08", need_any=False, recent_clauses={1}, verb="被關", shares=None)
        prices = [line["price"] for line in th["lines"] if line["kind"] == "price"]
        self.assertAlmostEqual(prices[0], 420 * (1 + (32 - s5) / 100), places=1)
        self.assertAlmostEqual(prices[1], max(420 * (1 + (25 - s5) / 100), c[-5] + 50), places=1)
        self.assertTrue(th["lines"][0]["text"].startswith("收盤 > "))

    def test_turnover_lines_only_when_any_clause_counts(self) -> None:
        self.add_bars("8111", [80.0, 82, 84, 86, 88, 90, 93.7], volume=17017)
        th = module.thresholds("8111", "OTC", "2026-10-08", need_any=True, recent_clauses={6}, verb="被關", shares=109_103_000)
        volume_lines = [line for line in th["lines"] if line["kind"] == "volume"]
        self.assertEqual(volume_lines[-1]["lots"], 5455)       # 第六款：週轉率 5%＝5,455.15 張 → 「> 5,455 張」（莊爸同）
        self.assertIn("成交量 > 5,455 張", volume_lines[-1]["text"])
        self.assertTrue(volume_lines[-1]["passed"])
        self.assertEqual(module._lots_over(5455.0), 5454)      # 剛好整數：5,455 張就達標 → 「> 5,454」
        th2 = module.thresholds("8111", "OTC", "2026-10-08", need_any=False, recent_clauses={6}, verb="被關", shares=109_103_000)
        self.assertFalse([line for line in th2["lines"] if line["kind"] == "volume"])   # 連 3 日第一款那條只看第一款


class PayloadTests(DbTestCase):
    def test_sections(self) -> None:
        now = datetime(2026, 10, 8, 20, 40, tzinfo=TW)

        def fake(url: str):
            if "jail-attention" in url:
                return {"fields": TPEX_ATTENTION["tables"][0]["fields"], "data": TPEX_ATTENTION["tables"][0]["data"] + [
                    [1, "5228", "鈺鎧", 3, "累積之最後成交價漲幅達35%(第一款)", "115/10/07", "61", "", ""],
                    [1, "5228", "鈺鎧", 3, "累積之最後成交價漲幅達36%(第一款)", "115/10/08", "61.3", "", ""],
                    [1, "6708", "天擎", 3, "累積之最後成交價漲幅達36%(第一款)", "115/10/08", "69.2", "", ""]]}
            if "jail-disposal" in url:
                return {"fields": TPEX_DISPOSAL["tables"][0]["fields"], "data": TPEX_DISPOSAL["tables"][0]["data"]}
            if "notice" in url:
                return TWSE_NOTICE
            if "punish" in url:
                return TWSE_PUNISH
            if "t187ap03" in url or "basics" in url:
                return []
            raise AssertionError(url)

        with patch.object(module, "_mirror_url", lambda name, volatile: name):
            module.collect(now=now, fetcher=fake, days=20)
        self.add_bars("5228", [45.0, 47, 50, 53, 56, 59, 61.3], volume=4797)
        fundamentals_daily.save_shares("OTC", {"5228": 38_060_000})
        p = module.build_payload(now)
        self.assertEqual((p["dataDate"], p["nextDay"], p["nextWeekday"]), ("2026-10-08", "2026-10-12", "一"))
        days = {d["date"]: [s["code"] for s in d["stocks"]] for w in p["weeks"] for d in w["days"]}
        self.assertEqual(days["2026-10-19"], ["6708"])      # 再次處置 10/12~10/16 → 10/19 出獄
        self.assertNotIn("6708", days["2026-10-14"])         # 第一次的迄日 10/13 隔天其實還在關
        self.assertIn("6213", days["2026-10-20"])
        self.assertTrue(next(d for w in p["weeks"] for d in w["days"] if d["date"] == "2026-10-09")["closed"])
        self.assertEqual([(j["code"], j["minutes"], j["daysLeft"]) for j in p["newJail"]], [("6708", 2, 6)])
        self.assertEqual([f["code"] for f in p["firstTime"]], ["3236", "6708"])   # 華新科 10/07 也有第一款，不算第一次
        suspect = next(s for s in p["suspects"] if s["code"] == "5228")
        self.assertEqual(suspect["status"], "連2 第一款 警告")
        self.assertTrue(suspect["lines"])
        self.assertNotIn("6708", [s["code"] for s in p["suspects"]])   # 今天剛公告入獄
        self.assertIn("5228 鈺鎧", p["copyText"])
        detail = module.stock_detail("6708", now)
        self.assertEqual(detail["jailCount"], 2)
        self.assertEqual(detail["tier"]["key"], "prior")
        self.assertEqual(detail["verdict"]["kind"], "jailed")

    def test_industry_gang_first_time_after_release_and_index_window(self) -> None:
        now = datetime(2026, 10, 8, 20, 40, tzinfo=TW)

        def notice(code: str, day: str, clauses: list[int], market: str = "OTC") -> dict:
            return {"code": code, "date": day, "market": market, "name": code, "clauses": clauses, "info": "", "close": 10.0}

        def punish(code: str, announce: str, start: str, end: str, market: str = "TSE") -> dict:
            return {"code": code, "start": start, "end": end, "market": market, "name": code, "announce": announce,
                    "condition": "連續三次", "measure": "第一次處置", "days": 5, "minutes": 2, "content": ""}

        module.save_notices([
            notice("7772", "2026-09-22", [1]), notice("7772", "2026-09-23", [1]), notice("7772", "2026-09-24", [1]),
            notice("7772", "2026-10-08", [1]),               # 9/24~10/02 關完出獄，10/08 是出獄後第一次
            notice("6174", "2026-10-05", [1]), notice("6174", "2026-10-08", [1]),   # 10/05 才中過，不算第一次
            notice("1111", "2026-09-01", [9], "TSE"), notice("1112", "2026-09-03", [9], "TSE"),
        ])
        module.save_punishes([
            punish("7772", "2026-09-23", "2026-09-24", "2026-10-02", "OTC"),
            punish("2030", "2026-10-01", "2026-10-02", "2026-10-08"), punish("2033", "2026-10-07", "2026-10-08", "2026-10-14"),
            punish("2243", "2026-06-22", "2026-06-23", "2026-07-06"), punish("2478", "2026-06-18", "2026-06-22", "2026-07-03"),
        ])
        companies = module.parse_companies([
            {"公司代號": "2030", "產業別": "10", "已發行普通股數或TDR原股發行股數": "100000000"},
            {"公司代號": "2033", "產業別": "10", "已發行普通股數或TDR原股發行股數": "50000000"},
            {"公司代號": "033569", "產業別": "10"},
        ])
        self.assertEqual(sorted(companies), ["2030", "2033"])
        module.save_companies("TSE", companies)
        module.save_companies("TSE", {"2030": {"industry": None, "shares": None}})   # 沒帶產業別的不要洗掉舊的
        p = module.build_payload(now)
        self.assertIn({"group": "鋼鐵", "stocks": ["2030", "2033"]},   # 彰源 10/08 還在關、10/12 出獄也算
                      [{"group": g["group"], "stocks": [s["code"] for s in g["stocks"]]} for g in p["gangs"]])
        self.assertEqual([f["code"] for f in p["firstTime"]], ["7772"])
        index = {r["code"] for r in p["index"]}
        self.assertTrue({"1112", "2243"} <= index)       # 近 25 個交易日被注意（9/03）、迄日在 95 天內（7/06）
        self.assertFalse({"1111", "2478"} & index)       # 9/01、7/03 太舊


if __name__ == "__main__":
    unittest.main()
