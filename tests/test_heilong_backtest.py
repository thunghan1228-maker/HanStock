"""下午報・黑龍回測：特徵計算、選股、出場方式、統計、建表與端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch
import brew_launch_history
import database
import fundamentals_daily
import heilong_backtest as module
import persistent_app
import swing_report
from trading_days import previous_trading_day

GROUPS = {"矽晶圓": [("6182", "合晶"), ("6488", "環球晶")], "半導體": [("2330", "台積電")]}


def trading_dates(last: str, count: int) -> list[str]:
    out = [last]
    d = date.fromisoformat(last)
    while len(out) < count:
        d = previous_trading_day(d)
        out.append(d.isoformat())
    return list(reversed(out))


def bar(d: str, o: float, h: float, l: float, c: float, v: int) -> module.Bar:
    return (d, o, h, l, c, v)


class FeatureTests(unittest.TestCase):
    def test_compute_features(self) -> None:
        dates = trading_dates("2026-09-24", 241)
        bars = [bar(d, 100, 101, 99, 100, 500) for d in dates[:-1]] + [bar(dates[-1], 104, 105, 101, 102, 1000)]
        out = module.compute_features(bars, {dates[-1], dates[-2], dates[0], dates[100]})
        self.assertEqual(sorted(out), sorted([dates[0], dates[100], dates[-2], dates[-1]]))
        last = out[dates[-1]]
        self.assertEqual((last["open"], last["high"], last["low"], last["close"], last["volume"]), (104, 105, 101, 102, 1000))
        self.assertEqual((last["score"], last["changePct"], last["hits20"], last["val5"], last["prevClose"]), (15, 2.0, 0, 0.6, 100))
        self.assertEqual(out[dates[-2]]["score"], 0)        # 第 240 根：剛好夠算、全平盤 0 分
        self.assertIsNone(out[dates[100]]["score"])         # 不到 240 根算不出
        self.assertEqual(out[dates[-2]]["changePct"], 0.0)
        self.assertEqual(out[dates[0]]["changePct"], None)  # 第一根沒有前一天
        self.assertEqual(out[dates[0]]["hits20"], 0)

    def test_hits20_counts_big_up_days(self) -> None:
        dates = trading_dates("2026-09-24", 25)
        closes = [100.0] * 25
        closes[10] = 109.0    # 第 11 天漲 9%（在近 20 日內）
        closes[3] = 110.0     # 第 4 天漲 10%（20 日之外）
        bars = [bar(d, c, c, c, c, 100) for d, c in zip(dates, closes)]
        out = module.compute_features(bars, {dates[-1]})
        self.assertEqual(out[dates[-1]]["hits20"], 1)

    def test_week_pct_uses_week_visible_that_day(self) -> None:
        weeks = [("2026-09-19", 1100.0), ("2026-09-12", 1000.0), ("2026-09-05", 800.0)]
        self.assertEqual(module._week_pct(weeks, "2026-09-22"), (10.0, "2026-09-19"))
        self.assertEqual(module._week_pct(weeks, "2026-09-19"), (25.0, "2026-09-12"))   # 週五當天還看不到那週的
        self.assertEqual(module._week_pct(weeks, "2026-09-08"), (None, "2026-09-05"))
        self.assertEqual(module._week_pct(weeks, "2026-09-01"), (None, None))

    def test_disposed(self) -> None:
        spans = {"6182": [("2026-09-20", "2026-09-30")], "2330": [("2026-09-26", "9999-12-31")]}
        self.assertTrue(module._disposed(spans, "6182", "2026-09-24"))
        self.assertFalse(module._disposed(spans, "6182", "2026-10-01"))
        self.assertTrue(module._disposed(spans, "2330", "2026-12-31"))
        self.assertFalse(module._disposed(spans, "6207", "2026-09-24"))


class OfficialScoreTests(unittest.TestCase):
    def test_official_score(self) -> None:
        self.assertIsNone(module.official_score([100.0] * 239))
        self.assertEqual(module.official_score([100.0] * 240), 6)            # 平盤：站上 0、創新高 6（同值算最近）、排列 0
        rising = [100 + i * 0.5 for i in range(300)]
        self.assertEqual(module.official_score(rising), 15)                   # 一路漲：6＋6＋3
        self.assertEqual(module.official_score([300 - i * 0.5 for i in range(300)]), 0)
        pullback = rising[:-4] + [rising[-5] - 1] * 4                          # 高點後拉回 4 天：新高 0、跌破 5 日線、排列仍在
        self.assertEqual(module.official_score(pullback), 8)
        self.assertEqual(module.official_score(rising, {p: sum(rising[-p:]) / p for p in module.MA_PERIODS}), 15)

    def test_select_rows_by_algo(self) -> None:
        base = {"date": "2026-09-24", "open": 100, "high": 101, "low": 98, "close": 99, "volume": 1000, "prevClose": 100, "changePct": -1.0,
                "hits20": 1, "val5": 5.0, "group": "矽晶圓", "weekPct": None, "weekDate": None, "disposed": False}
        rows = {"A": {**base, "code": "A", "score": 8, "score2": 12, "groupAvg": 7.0, "groupAvg2": 11.0},
                "B": {**base, "code": "B", "score": 12, "score2": 8, "groupAvg": 11.0, "groupAvg2": 7.0}}
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({}))], ["B"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"algo": "official"}))], ["A"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"score": 0, "sort": "gavg", "algo": "official"}))], ["A", "B"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"score": 0, "sort": "gavg"}))], ["B", "A"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"score": 0, "gavg": 10, "algo": "official"}))], ["A"])
        with self.assertRaises(ValueError):
            module.normalize_params({"algo": "x"})


class ExitTests(unittest.TestCase):
    def test_exits(self) -> None:
        x = module.exits(100, 97, (101, 104, 100, 102), 3)
        self.assertEqual(x, {"close": 2.0, "tp": 3.0, "sl": 2.0, "both": 3.0, "open": 1.0, "ohl": 1.0, "hitTp": True, "hitSl": False})
        x = module.exits(100, 97, (104, 105, 103, 104), 3)    # 開盤就到目標：開盤賣
        self.assertEqual((x["tp"], x["both"], x["ohl"]), (4.0, 4.0, 4.0))
        x = module.exits(100, 97, (96, 98, 95, 97), 3)        # 開盤就破黑K低：開盤賣
        self.assertEqual((x["close"], x["tp"], x["sl"], x["both"], x["ohl"], x["hitTp"], x["hitSl"]), (-3.0, -3.0, -4.0, -4.0, -3.0, False, True))
        x = module.exits(100, 97, (99, 104, 96, 101), 3)      # 同一天兩個都碰到：保守算停損
        self.assertEqual((x["close"], x["tp"], x["sl"], x["both"], x["ohl"]), (1.0, 3.0, -3.0, -3.0, 1.0))
        x = module.exits(100, 97, (99, 101, 98, 100.5), None) # 沒停利：停利＝收盤、兩個一起＝破黑低
        self.assertEqual((x["tp"], x["both"], x["hitTp"]), (0.5, 0.5, None))

    def test_stats(self) -> None:
        self.assertEqual(module.stats([1.0, -2.0, 3.0], 50), {"count": 3, "avg": 0.67, "median": 1.0, "win": 67, "wins": 2, "total": 1.0, "worst": -2.0, "best": 3.0})
        self.assertEqual(module.stats([], 50)["count"], 0)

    def test_select_rows(self) -> None:
        def row(code, **kw):
            base = {"code": code, "date": "2026-09-24", "open": 100, "high": 101, "low": 98, "close": 99, "volume": 1000, "prevClose": 100, "changePct": -1.0,
                    "score": 12, "hits20": 1, "val5": 5.0, "group": "矽晶圓", "groupAvg": 11.0, "weekPct": 6.0, "weekDate": "2026-09-19", "disposed": False}
            base.update(kw)
            return base
        rows = {
            "A": row("A"),
            "B": row("B", score=9),                       # 分數不夠
            "C": row("C", open=98, close=99),             # 紅K
            "D": row("D", changePct=4.0),                 # 漲太多
            "E": row("E", disposed=True),                 # 處置中
            "F": row("F", weekPct=None, score=15, changePct=-3.0),
            "G": row("G", score=15, changePct=-2.0, hits20=0, val5=1.0),
        }
        p = module.normalize_params({})
        self.assertEqual([r["code"] for r in module.select_rows(rows, p)], ["F", "G", "A"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"exdispo": 0, "sort": "drop"}))], ["F", "G", "A", "E"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"week": 5}))], ["G", "A"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"hits": 1, "val": 3}))], ["F", "A"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"cap": 1}))], ["F"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"k": "red"}))], ["C"])
        self.assertEqual([r["code"] for r in module.select_rows(rows, module.normalize_params({"k": "any", "min": -5, "max": 5}))], ["F", "G", "A", "C", "D"])

    def test_normalize_params(self) -> None:
        p = module.normalize_params({"score": "12", "k": "any", "min": "-5", "max": "5", "week": "", "tp": "5", "days": 0, "exdispo": "0"})
        self.assertEqual((p["score"], p["k"], p["min"], p["max"], p["week"], p["tp"], p["days"], p["exdispo"]), (12, "any", -5.0, 5.0, None, 5.0, 0, False))
        for bad in ({"k": "foo"}, {"score": 20}, {"min": 5, "max": -5}, {"tp": 0}, {"sort": "x"}, {"mine": "x"}, {"cap": -1}, {"week": "abc"}):
            with self.assertRaises(ValueError):
                module.normalize_params(bad)
        self.assertEqual(module.normalize_params({"days": 999})["days"], 0)


class RebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.patches = [patch.object(m, "STOCK_GROUPS", GROUPS) for m in (module, brew_launch, brew_launch_history)]
        for p in self.patches:
            p.start()
        brew_launch_history._group_by_code.clear()
        self.dates = trading_dates("2026-09-24", 243)
        d = self.dates
        rows = []
        # 合晶：平盤 240 天 → D（第 241 天）黑K、D+1、D+2
        rows += [("6182", x + "T00:00:00", 100, 101, 99, 100, 500) for x in d[:240]]
        rows += [("6182", d[240] + "T00:00:00", 104, 105, 101, 102, 1000),
                 ("6182", d[241] + "T00:00:00", 103, 106, 100, 104, 1200),
                 ("6182", d[242] + "T00:00:00", 104, 108, 103, 107, 900)]
        # 環球晶：只有 60 天，算不出分數
        rows += [("6488", x + "T00:00:00", 500, 505, 495, 500, 300) for x in d[-60:]]
        # 台積電：完全平盤（開＝收，不是黑K也不是紅K）
        rows += [("2330", x + "T00:00:00", 1000, 1001, 999, 1000, 20000) for x in d]
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        # 集保週：D 之前兩週（9/11、9/18 都是週五）
        fundamentals_daily.save_tdcc("2026-09-11", [("6182", 12, 10, 1_000_000, 10.0), ("6182", 15, 2, 1_000_000, 10.0)])
        fundamentals_daily.save_tdcc("2026-09-18", [("6182", 12, 10, 1_200_000, 12.0), ("6182", 15, 2, 1_000_000, 10.0)])
        swing_report.log_dispositions([{"code": "2330", "start": "2026-09-23", "end": "2026-10-06", "name": "台積電", "reason": "測試", "source": "test"}])

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        for p in self.patches:
            p.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_rebuild_and_backtest(self) -> None:
        d = self.dates
        result = module.rebuild()
        self.assertEqual(result["date"], d[-1])
        self.assertEqual(len(result["rebuilt"]), module.HISTORY_DAYS)
        self.assertEqual(result["dates"], module.HISTORY_DAYS)
        dates, table = module.load_rows()
        self.assertEqual(len(dates), module.HISTORY_DAYS)
        row = table[d[240]]["6182"]
        self.assertEqual((row["score"], row["changePct"], row["hits20"], row["group"], row["groupAvg"], row["weekPct"], row["weekDate"], row["disposed"]),
                         (15, 2.0, 0, "矽晶圓", 15.0, 10.0, "2026-09-18", False))
        self.assertEqual((row["score2"], row["groupAvg2"]), (15, 15.0))          # 官網式：站上 6＋新高 6＋排列 3
        self.assertEqual((table[d[239]]["6182"]["score"], table[d[239]]["6182"]["score2"]), (0, 6))   # 平盤那天
        self.assertIsNone(table[d[240]]["6488"]["score"])
        self.assertTrue(table[d[241]]["2330"]["disposed"])     # 9/23 起處置
        self.assertFalse(table[d[240]]["2330"]["disposed"])
        # 第二次只重算最新 3 天
        again = module.rebuild()
        self.assertEqual(again["rebuilt"], d[-3:])
        self.assertEqual(again["rows"], result["rows"])
        self.assertEqual(module.collector_status()["lastDate"], d[-1])

        r = module.backtest({"days": 10})
        self.assertEqual(r["status"], "ok")
        self.assertEqual((r["date"], r["window"]["days"], r["window"]["backtestDays"]), (d[-1], 10, 1))
        self.assertEqual(r["stats"]["trades"], 1)
        self.assertEqual((r["stats"]["hitTp"], r["stats"]["hitSl"], r["stats"]["perDay"]), (100, 100, 1.0))
        methods = {m["key"]: m for m in r["stats"]["methods"]}
        self.assertEqual(methods["close"]["avg"], 1.96)
        self.assertEqual(methods["tp"]["avg"], 3.0)
        self.assertEqual((methods["sl"]["avg"], methods["sl"]["win"]), (-0.98, 0))
        self.assertEqual(methods["both"]["avg"], -0.98)
        self.assertEqual(methods["open"]["avg"], 0.98)
        self.assertEqual(methods["ohl"]["avg"], 0.98)
        self.assertEqual((methods["d2"]["count"], methods["d2"]["avg"], methods["d2"]["total"]), (1, 4.9, 2.5))
        self.assertEqual(methods["tp"]["label"], "停利出場（+3%）")
        self.assertEqual((r["burst"]["d1"]["samples"], r["burst"]["d1"]["avg"], r["burst"]["d1"]["win"], r["burst"]["d1"]["ge5"], r["burst"]["d1"]["openAvg"], r["burst"]["d1"]["closeAvg"]),
                         (1, 3.92, 100, 0, 0.98, 1.96))
        self.assertEqual((r["burst"]["d2"]["samples"], r["burst"]["d2"]["avg"], r["burst"]["d2"]["ge5"], r["burst"]["d2"]["closeAvg"]), (1, 5.88, 100, 4.9))
        self.assertEqual(r["burst"]["d3"]["samples"], 0)
        self.assertEqual(len(r["curve"]), 1)
        self.assertEqual((r["curve"][0]["date"], r["curve"][0]["count"], r["curve"][0]["close"], r["curve"][0]["cum"]["tp"], r["curve"][0]["max2"], r["curve"][0]["n3"]),
                         (d[240], 1, 1.96, 3.0, 5.88, 0))
        self.assertEqual((r["today"]["date"], r["today"]["count"]), (d[-1], 0))     # 最新那天合晶是紅K
        self.assertEqual([x["date"] for x in r["daily"]], list(reversed(d[-10:])))  # 新到舊
        day = r["daily"][2]
        self.assertEqual((day["date"], day["count"], day["withNext"], day["avg"]["close"]), (d[240], 1, 1, 1.96))
        pick = day["rows"][0]
        self.assertEqual((pick["code"], pick["name"], pick["group"], pick["entry"], pick["lowK"], pick["target"]), ("6182", "合晶", "矽晶圓", 102.0, 101.0, 105.06))
        self.assertEqual((pick["score"], pick["score2"], pick["groupAvg"], pick["groupAvg2"]), (15, 15, 15.0, 15.0))
        r3 = module.backtest({"days": 10, "algo": "official"})
        self.assertEqual((r3["params"]["algo"], r3["stats"]["trades"]), ("official", 1))
        self.assertEqual(pick["next"], {"date": d[241], "open": 103.0, "high": 106.0, "low": 100.0, "close": 104.0})
        self.assertEqual((pick["hitTp"], pick["hitSl"], pick["gap"]), (True, True, False))
        self.assertEqual(pick["exits"], {"close": 1.96, "tp": 3.0, "sl": -0.98, "both": -0.98, "open": 0.98, "ohl": 0.98, "d2": 4.9})
        # K 棒不限：最新那天（紅K、+2.88%）進今日名單，還沒有 D+1
        r2 = module.backtest({"k": "any", "days": 20, "tp": 5, "mine": "tp"})
        self.assertEqual([x["code"] for x in r2["today"]["rows"]], ["6182"])
        self.assertIsNone(r2["today"]["rows"][0]["next"])
        self.assertEqual(r2["today"]["rows"][0]["target"], 112.35)
        self.assertEqual(r2["stats"]["trades"], 2)
        self.assertEqual(r2["stats"]["mine"], "tp")
        self.assertEqual(r2["window"]["backtestDays"], 2)

    def test_gap_leaves_next_blank(self) -> None:
        d = self.dates
        with database.get_connection() as c:   # D+1 開盤只剩一半（分割）
            c.execute("UPDATE bars_1d SET open = 51, high = 53, low = 50, close = 52 WHERE stock_code = '6182' AND bar_time = ?", (d[241] + "T00:00:00",))
        module.rebuild()
        r = module.backtest({"days": 10})
        self.assertEqual(r["stats"]["trades"], 0)
        pick = [x for day in r["daily"] for x in day["rows"]][0]
        self.assertEqual((pick["code"], pick["gap"], pick["next"], pick["exits"]), ("6182", True, None, None))

    def test_endpoints(self) -> None:
        module.rebuild()
        client = TestClient(persistent_app.app)
        r = client.get("/api/hub/heilong", params={"days": 10, "min": -10, "max": 3})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["status"], body["params"]["min"], body["params"]["max"], body["stats"]["trades"]), ("ok", -10.0, 3.0, 1))
        self.assertEqual(client.get("/api/hub/heilong", params={"k": "foo"}).status_code, 422)
        self.assertEqual(client.get("/api/hub/heilong", params={"week": "abc"}).status_code, 422)
        self.assertEqual(client.get("/api/hub/heilong", params={"algo": "foo"}).status_code, 422)
        self.assertEqual(client.get("/api/hub/heilong", params={"algo": "official"}).json()["params"]["algo"], "official")
        s = client.get("/api/hub/heilong/status").json()
        self.assertEqual((s["status"], s["lastDate"]), ("ok", self.dates[-1]))
        rb = client.post("/api/hub/heilong/rebuild", params={"force": 1}).json()
        self.assertEqual(len(rb["result"]["rebuilt"]), module.HISTORY_DAYS)

    def test_old_table_gets_score2_backfilled(self) -> None:
        module.rebuild()
        with database.get_connection() as c:
            c.execute("UPDATE heilong_daily SET score2 = NULL, group_avg2 = NULL")
        again = module.rebuild()
        self.assertEqual(len(again["rebuilt"]), module.HISTORY_DAYS)          # 缺官網式分數 → 整張重算
        dates, table = module.load_rows()
        self.assertEqual(table[self.dates[240]]["6182"]["score2"], 15)

    def test_empty_table(self) -> None:
        r = module.backtest({})
        self.assertEqual(r["status"], "empty")
        self.assertIn("rules", r)


class SwingHookTests(unittest.TestCase):
    def test_run_once_rebuilds_heilong(self) -> None:
        with patch.object(swing_report, "_latest_bar_date", return_value="2026-09-24"), \
             patch.object(swing_report, "report_dates", return_value=["2026-09-24"]), \
             patch.object(swing_report, "bar_dates", return_value=["2026-09-24"]), \
             patch.object(swing_report, "refresh_report", return_value={"status": "ok"}), \
             patch.object(module, "rebuild", return_value={"date": "2026-09-24", "rebuilt": ["2026-09-24"]}) as rebuild:
            result = swing_report.run_once()
        self.assertEqual(rebuild.call_count, 1)
        self.assertEqual(result["heilong"]["rebuilt"], ["2026-09-24"])


if __name__ == "__main__":
    unittest.main()
