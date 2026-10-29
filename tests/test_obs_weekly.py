"""收盤觀察股 週分析：觀察池（創高黑龍 10 天 ≥2 次＋均線分 ≥15）、名單異動與刪除原因、段落分類（高檔／月線下／即將穿越／危險／功課重點）、
族群總覽、四週回測、功課提醒。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database
import heilong_backtest
import ma_rank
import obs_weekly as module

GROUPS = {
    "甲": [("1111", "一號"), ("2222", "二號")],
    "乙": [("3333", "三號"), ("4444", "四號")],
    "丙": [("6666", "六號"), ("7777", "七號")],
    "金融股": [("2881", "富邦金")],
    "千元": [("5555", "五號")],
}
START, END = date(2026, 9, 14), date(2026, 10, 28)
DAYS = [(START + timedelta(days=i)).isoformat() for i in range((END - START).days + 1) if (START + timedelta(days=i)).weekday() < 5]
D = DAYS[-1]                    # 2026-10-28 週三
BASE = "2026-10-23"             # 上一週最後一個交易日
LATE = "2026-10-26"             # 2222 從這天起天天收黑龍


def close_of(code: str, i: int) -> float:
    n = len(DAYS)
    if code == "6666":                                   # 前面平盤 100，最後 5 天跌到 95：月線下、距月線不到 9%
        return 100.0 if i < n - 5 else 95.0
    if code == "7777":                                   # 最後 21 天從 100 一路漲到 140：離月線遠、20 日漲 ≥30%
        return 100.0 if i < n - 21 else 100.0 + (i - (n - 21)) * 2.0
    return 100.0


def row_of(code: str, i: int) -> tuple:
    day = DAYS[i]
    close = close_of(code, i)
    if code in ("1111", "6666", "7777"):
        dragon, score = True, 15
    elif code == "2222":
        dragon, score = day >= LATE, 15
    elif code == "3333":
        dragon, score = day <= "2026-09-29", 15 if day <= "2026-09-29" else 12   # 9/29 以後再也沒進過觀察池：10/23 的名單還在（20 個交易日內）、今天已經滿 20 天
    elif code == "4444":
        dragon, score = day == "2026-10-20", 15                              # 10 天只被選到 1 次
    else:
        dragon, score = False, 15
    open_ = close + 1 if dragon else close - 1
    chg = -1.0 if dragon else 1.0
    if code == "7777" and day == D:
        chg = 9.8
    return (day, code, open_, max(open_, close) + 0.5, min(open_, close) - 0.5, close, 1000, chg, score, 0b111111, 0b111111, 3)


class ObsWeeklyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db"),
            patch.object(ma_rank, "STOCK_GROUPS", GROUPS),
        ]
        for p in self.patches:
            p.start()
        database.initialize_database()
        rows = [row_of(code, i) for i in range(len(DAYS)) for code in ("1111", "2222", "3333", "4444", "6666", "7777", "5555", "2881")]
        with database.get_connection() as c:
            heilong_backtest._schema(c)
            c.executemany(
                """INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, change_pct, score2, ma_bits, hi_bits, align_n)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
        module._cache.clear()
        ck = lambda fund, chip, tech: {"scores": {"fund": fund, "chip": chip, "tech": tech, "overall": 60, "overallLabel": "中上"}}  # noqa: E731
        self.mocks = [
            patch.object(module, "_institutional", lambda day, codes: (
                {"1111": {"inst5": 500, "inst10": 900}, "2222": {"inst5": -50, "inst10": -58262}, "3333": {"inst5": -10, "inst10": -2000},
                 "6666": {"inst5": -20, "inst10": -67}}, ["2026-10-27", "2026-10-28"])),
            patch("fundamentals_daily.tdcc_summary", lambda codes, weeks=6: {
                "1111": {"date": "2026-10-22", "bigChangePct": 0.9, "weeks": 2}, "2222": {"date": "2026-10-22", "bigChangePct": -39.1, "weeks": 0},
                "6666": {"date": "2026-10-22", "bigChangePct": -0.9, "weeks": 0}}),
            patch("fundamentals_daily.latest_revenue", lambda codes: {"1111": {"ym": "2026-09", "yoy": 50.0}, "6666": {"ym": "2026-09", "yoy": -7.3}}),
            patch("financials.all_singles", lambda: {"1111": [{"season": "2026Q2", "label": "26Q2", "eps": 6.7, "margin": 25.3}],
                                                       "6666": [{"season": "2026Q2", "label": "26Q2", "eps": -1.88, "margin": -5.0}]}),
            patch("stock_checkup.load_rows", lambda day, codes=None: {
                "1111": ck(70, 70, 100), "2222": ck(40, 50, 100), "6666": ck(24, 22, 53), "7777": ck(80, 80, 100)}),
        ]
        for p in self.mocks:
            p.start()

    def tearDown(self) -> None:
        for p in self.mocks + self.patches:
            p.stop()
        module._cache.clear()
        self.temp_dir.cleanup()

    def test_pool_rules(self) -> None:
        dates = module._dates()
        rows = module._load_days(dates, ["1111", "2222", "3333", "4444", "6666", "7777"])
        pool = module.pool_at(D, dates, rows)
        self.assertEqual(sorted(pool), ["1111", "2222", "6666", "7777"])      # 3333 均線分 12、4444 只被選到 1 次
        self.assertEqual(pool["2222"], 3)
        base = module.pool_at(BASE, dates, rows)
        self.assertEqual(sorted(base), ["1111", "6666", "7777"])              # 2222 那時還沒有黑龍、3333 均線分已經掉到 12
        # 名單＝近 20 個交易日進過池的：3333 最後一次進池是 9/29，10/23 的名單還在、今天（10/28）已經滿 20 天
        self.assertEqual(module.list_at(BASE, dates, rows)["3333"]["last"], "2026-09-29")
        self.assertNotIn("3333", module.list_at(D, dates, rows))
        self.assertEqual(sorted(module.list_at(D, dates, rows)), ["1111", "2222", "6666", "7777"])

    def test_weekly_list_dates(self) -> None:
        got = module._weekly_list_dates(module._dates(), D)
        self.assertEqual(got, ["2026-10-23", "2026-10-16", "2026-10-09", "2026-10-02"])

    def test_diff_and_reasons(self) -> None:
        out = module.build()
        self.assertEqual((out["date"], out["n"]), (D, 4))
        diff = out["diff"]
        self.assertEqual(diff["base"], BASE)
        self.assertEqual([i["code"] for i in diff["added"]], ["2222"])
        self.assertEqual([i["code"] for i in diff["removed"]], ["3333"])
        self.assertEqual(diff["kept"], 3)
        reasons = diff["removed"][0]["reasons"]
        self.assertIn("近 20 個交易日沒再進觀察池（最後一次 09/29）", reasons)
        self.assertIn("法人連賣", reasons)
        self.assertTrue(next(i for i in out["items"] if i["code"] == "2222")["isNew"])

    def test_flags_and_sections(self) -> None:
        out = module.build()
        by = {i["code"]: i for i in out["items"]}
        six, seven, one = by["6666"], by["7777"], by["1111"]
        self.assertTrue(six["below"] and six["crossing"] and six["danger"])          # 95 < 月線 99.x、距月線 ≈ -4.7%；健診 24/22/53 都 <60
        self.assertLess(six["dist20"], 0)
        self.assertTrue(seven["high"] and seven["lit"] and not seven["below"])       # 20 日漲 +40%、漲幅 9.8%
        self.assertEqual(seven["ret20"], 40.0)
        self.assertTrue(one["homework"] and not one["high"] and not one["below"])    # 均線 15、籌碼 +0.9%、基本面 70、EPS 6.7
        self.assertFalse(by["2222"]["homework"])                                      # 基本面 40
        self.assertFalse(by["2222"]["chipOk"])                                        # 籌碼 -39.1%
        sec = out["sections"]
        self.assertEqual(sec["homework"], ["1111"])
        self.assertEqual(sec["high"], ["7777"])
        self.assertEqual(sec["below"], ["6666"])
        self.assertEqual(sec["danger"], ["6666"])
        self.assertEqual(sec["watch"], ["2222"])
        self.assertEqual(one["scoreSeries"], [15] * 5)

    def test_groups(self) -> None:
        out = module.build()
        names = [g["name"] for g in out["groups"]]
        self.assertEqual(names, ["丙", "甲"])                                          # 各 2 檔（同檔數照族群名排）；乙 0 檔不列
        bing = next(g for g in out["groups"] if g["name"] == "丙")
        self.assertEqual((bing["n"], bing["above"], bing["withMa"]), (2, 1, 2))
        self.assertIn("1/2 檔在月線下", bing["note"])

    def test_small_groups_go_to_other(self) -> None:
        def item(code: str, grp: str, **kw):
            base = {"code": code, "name": code, "grp": grp, "score": 8, "ma20": 10.0, "below": False, "dist20": 1.0, "revYoy": None,
                    "chipWeeks": 0, "inst5": None}
            base.update(kw)
            return base
        items = [item("1", "甲"), item("2", "甲", below=True, dist20=-3.0), item("3", "乙"), item("4", "丙")]
        rows = module._group_overview(items)
        self.assertEqual([(g["name"], g["n"]) for g in rows], [("甲", 2), ("其他", 2)])
        self.assertEqual(rows[0]["above"], 1)
        self.assertEqual([i["gname"] for i in items], ["甲", "甲", "其他", "其他"])

    def test_backtest(self) -> None:
        out = module.build()
        weeks = out["backtest"]["weeks"]
        self.assertEqual([w["date"] for w in weeks], ["2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23"])   # 舊到新
        last = weeks[-1]
        self.assertEqual(last["n"], 4)                       # 1111、3333、6666、7777
        rets = {x["code"]: x["ret"] for x in last["best"] + last["worst"]}
        self.assertEqual(rets["7777"], 4.5)                   # 10/23 收 134 → 10/28 收 140
        self.assertIn("winRate", last)

    def test_reminders_mention_crossing(self) -> None:
        out = module.build()
        text = "\n".join(out["reminders"])
        self.assertIn("即將穿越", text)
        self.assertIn("條件單", text)
        self.assertIn("三者同時成立才上功課", text)
        self.assertIn("法人 10 日大賣", text)

    def test_missing_date(self) -> None:
        with self.assertRaises(LookupError):
            module.build("2026-01-01")
        with self.assertRaises(LookupError):
            module.build(DAYS[3])                             # 前面不到 10 個交易日

    def test_endpoint(self) -> None:
        from fastapi.testclient import TestClient

        import persistent_app

        client = TestClient(persistent_app.app)
        r = client.get("/api/hub/obs-weekly")
        self.assertEqual((r.status_code, r.json()["date"], r.json()["n"]), (200, D, 4), r.text[:300])
        self.assertEqual(client.get("/api/hub/obs-weekly", params={"date": "2026-01-02"}).status_code, 404)
        self.assertEqual(client.get("/api/hub/obs-weekly", params={"date": "10/08"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
