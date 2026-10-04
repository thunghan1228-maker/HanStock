"""創高黑選股：參數、條件池、每日新進、每週名單、加減碼出場、實績、模擬帳戶、選股頁、端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch
import brew_launch_history
import database
import heilong_backtest
import heilong_picker as picker
import persistent_app


def weekdays(start: str, count: int) -> list[str]:
    out = []
    d = date.fromisoformat(start)
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_panel(dates: list[str], data: dict[str, list], *, shares: dict[str, int] | None = None, bench: list[float] | None = None,
               groups: dict[str, str] | None = None) -> picker.Panel:
    """data＝{代號: 每天 (開, 高, 低, 收, 漲跌%, 均線分, 創高天數, 5日均成交值) 或 None（沒成交）}。"""
    panel = picker.Panel("test", dates)
    for code, rows in data.items():
        s = picker.Series(code, len(dates))
        s.name = f"股{code}"
        s.shares = (shares or {}).get(code, 10 ** 9)   # 預設 10 億股：市值一定夠
        s.group = (groups or {}).get(code, "")
        s.in_group = bool(s.group)
        for i, row in enumerate(rows):
            if row is None:
                continue
            o, h, l, c, chg, score, hilen, val5 = row
            s.o[i], s.h[i], s.l[i], s.c[i] = o, h, l, c
            if chg is not None:
                s.chg[i] = chg
            s.score[i] = score
            s.hilen[i] = hilen
            s.val5[i] = val5
        panel.series[code] = s
    if bench:
        for i, x in enumerate(bench):
            panel.bench[i] = x
    panel.build_weeks()
    return panel


def flat(c: float, *, score: int = 12, hilen: int = 1, val5: float = 2.0, chg: float = 0.0, black: bool = False) -> tuple:
    o = c * 1.01 if black else c
    return (o, max(o, c) + 0.5, min(o, c) - 0.5, c, chg, score, hilen, val5)


P = dict(picker.DEFAULTS)


def params(**kw) -> dict:
    return picker.normalize_params({**{k: v for k, v in P.items()}, **kw})


class ParamTests(unittest.TestCase):
    def test_defaults_and_validation(self) -> None:
        p = picker.normalize_params({})
        self.assertEqual((p["hi"], p["within"], p["score"], p["weekN"], p["lots"], p["rma"], p["xma"], p["maxpos"]), (20, 6, 10, 6, 3, 5, 10, 6))
        q = picker.normalize_params({"hi": "40", "exdispo": "0", "spct": "9.5", "wsort": "val"})
        self.assertEqual((q["hi"], q["exdispo"], q["spct"], q["wsort"]), (40, False, 9.5, "val"))
        for bad in ({"score": 16}, {"wsort": "x"}, {"rma": 7}, {"fee": 2}, {"lots": 0}, {"hi": "abc"}, {"black": "red"}):
            with self.assertRaises(ValueError):
                picker.normalize_params(bad)

    def test_fee_rates(self) -> None:
        self.assertEqual(picker.fee_rates(0), (0.0, 0.0))
        self.assertAlmostEqual(picker.round_trip_pct(0.28), 0.0798 + 0.3, places=4)   # 2.8 折：來回約 0.38%


class PoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dates = weekdays("2026-08-03", 12)

    def test_conditions(self) -> None:
        rows = [flat(100) for _ in self.dates]
        rows[3] = flat(110, hilen=25, chg=9.0)        # 第 4 天創 25 日新高、大漲 9%
        rows[4] = flat(112, chg=8.5)                  # 第 5 天再大漲（近 10 天 2 次 >8%）
        panel = make_panel(self.dates, {"A": rows})
        pool = picker.pool_flags(panel, params(hi=20, within=6, sdays=10, spct=8, stimes=2))
        self.assertEqual(list(pool.flags["A"]), [0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0])   # 第 5～9 天（創高後 6 天內、強勢 2 次）
        self.assertEqual(pool.entrants(4), ["A"])
        self.assertEqual(pool.entrants(5), [])
        self.assertEqual(pool.tracked(6, 10), ["A"])
        self.assertEqual(pool.tracked(4, 10), [])        # 上榜當天還不算追蹤（隔天起）
        # 均線分不夠、成交值不夠、市值不夠、處置中都不算
        self.assertNotIn("A", picker.pool_flags(panel, params(score=13)).flags)
        self.assertNotIn("A", picker.pool_flags(panel, params(val=3)).flags)
        panel.series["A"].shares = 1000
        self.assertNotIn("A", picker.pool_flags(panel, params(mcap=20)).flags)
        self.assertIn("A", picker.pool_flags(panel, params(mcap=0)).flags)
        panel.series["A"].shares = 10 ** 9
        panel.series["A"].disposed[5] = 1
        flags = picker.pool_flags(panel, params(exdispo=True, sdays=0)).flags["A"]
        self.assertEqual(flags[5], 0)
        self.assertEqual(flags[4], 1)
        self.assertEqual(picker.pool_flags(panel, params(exdispo=False, sdays=0)).flags["A"][5], 1)
        # 族群表內：不在族群的不抓
        self.assertEqual(picker.pool_flags(panel, params(scope="groups")).flags, {})
        self.assertEqual(picker.run_start(pool.flags["A"], 8), 4)

    def test_within_when_today(self) -> None:
        rows = [flat(100) for _ in self.dates]
        rows[3] = flat(110, hilen=25)
        panel = make_panel(self.dates, {"A": rows})
        flags = picker.pool_flags(panel, params(within=1, sdays=0)).flags["A"]
        self.assertEqual(list(flags), [0, 0, 0, 1] + [0] * 8)   # 「當天」：只有創高那天


class WeekListTests(unittest.TestCase):
    def test_pick_on_last_day_of_previous_week(self) -> None:
        dates = weekdays("2026-08-03", 10)        # 兩週：8/3～8/7、8/10～8/14
        data = {}
        for code, score in (("A", 15), ("B", 13), ("C", 11)):
            data[code] = [flat(100, score=score, hilen=30) for _ in dates]
        panel = make_panel(dates, data)
        pool = picker.pool_flags(panel, params(sdays=0))
        chosen, alternates, pick_day = picker.week_list(panel, pool, params(sdays=0, weekN=2), 1)
        self.assertEqual((chosen, alternates, dates[pick_day]), (["A", "B"], ["C"], "2026-08-07"))
        # 改依成交值排
        panel.series["C"].val5[4] = 9.0
        chosen, _alt, _ = picker.week_list(panel, pool, params(sdays=0, weekN=1, wsort="val"), 1)
        self.assertEqual(chosen, ["C"])
        # 使用者改過那一週的名單
        chosen, alternates, _ = picker.week_list(panel, pool, params(sdays=0, weekN=2), 1, {"2026-08-10": ["C"]})
        self.assertEqual((chosen, alternates), (["C"], ["A", "B"]))
        self.assertEqual(picker.week_list(panel, pool, params(), 0), ([], [], None))   # 第一週沒有上一週可以選
        self.assertEqual(picker.week_label(panel, 2)["label"], "8/17~8/21")             # 下一週


class PositionTests(unittest.TestCase):
    def test_reduce_once_rearm_exit_and_add(self) -> None:
        dates = weekdays("2026-08-03", 30)
        # 前 20 天 90 → 99.5 慢慢漲，第 21 天（index 20）100 進場
        closes = [90 + k * 0.5 for k in range(20)] + [100, 101, 98, 101, 103, 102, 90, 89, 88, 87]
        rows = [flat(c) for c in closes]
        rows[21] = flat(101, black=True)
        panel = make_panel(dates, {"A": rows})
        s = panel.series["A"]
        p = params(lots=3, rma=5, xma=20, tpr=0)
        rates = (0.0, 0.0)
        pos = picker.Position("A", 20, "list")
        pos.buy(s.c[20], 100_000, 0.0)
        acts = picker.step_position(pos, s, 21, p, rates, 100_000)      # 收黑 101：還在 5 日線上 → 加碼
        self.assertEqual([a["type"] for a in acts], ["add"])
        self.assertEqual(pos.batches, 2)
        acts = picker.step_position(pos, s, 22, p, rates, 100_000)      # 98 跌破 5 日線 → 減 1 批
        self.assertEqual([a["type"] for a in acts], ["reduce"])
        self.assertEqual(picker.step_position(pos, s, 22, p, rates, 100_000), [])   # 同一段跌破不會再減
        picker.step_position(pos, s, 23, p, rates, 100_000)            # 站回 5 日線：重新上膛
        self.assertTrue(pos.armed)
        acts = picker.step_position(pos, s, 26, p, rates, 100_000)      # 90 跌破 20 日線 → 全部出
        self.assertEqual([a["type"] for a in acts], ["exit"])
        self.assertFalse(pos.shares)

    def test_take_profit_reduce(self) -> None:
        dates = weekdays("2026-08-03", 25)
        closes = [100.0] * 21 + [104, 108, 111, 112]
        panel = make_panel(dates, {"A": [flat(c) for c in closes]})
        s = panel.series["A"]
        p = params(tpr=10, reduce=False)
        pos = picker.Position("A", 20, "list")
        pos.buy(100, 100_000, 0)
        pos.buy(100, 100_000, 0)
        self.assertEqual(picker.step_position(pos, s, 22, p, (0, 0), 100_000), [])
        acts = picker.step_position(pos, s, 23, p, (0, 0), 100_000)
        self.assertEqual((acts[0]["type"], acts[0]["reason"]), ("reduce", "停利+10%・減1批"))
        self.assertEqual(picker.step_position(pos, s, 24, p, (0, 0), 100_000), [])    # 停利減碼只一次
        self.assertAlmostEqual(pos.realized, 1000 * 11, places=6)


class PerfTests(unittest.TestCase):
    def test_signal_exits(self) -> None:
        dates = weekdays("2026-08-03", 40)
        closes = [100.0] * 25 + [102, 101, 103, 105, 104, 106, 99, 98, 97, 96, 95, 94, 93, 92, 91]
        rows = [flat(c, score=12) for c in closes]
        rows[24] = flat(100, score=12, hilen=30, chg=9)    # 第 25 天：創高＋上榜
        rows[23] = flat(100, score=12, chg=9)
        rows[26] = flat(101, score=12, black=True)         # 第 27 天收黑 → 收盤 101 進場
        panel = make_panel(dates, {"A": rows})
        p = params(sdays=10, stimes=2, spct=8, within=6, track=10, fee=0, range=0, ptp=5, lots=1, reduce=False, xma=5)
        result = picker.perf(panel, p)
        self.assertEqual((result["signals"], result["entered"]), (1, 1))
        m = {x["key"]: x for x in result["methods"]}
        self.assertAlmostEqual(m["d1"]["avg"], round((103 / 101 - 1) * 100, 2))
        self.assertAlmostEqual(m["h3"]["avg"], round((104 / 101 - 1) * 100, 2))
        self.assertEqual(m["m5"]["days"], 5)                 # 第 32 天 99 跌破 5 日線
        self.assertAlmostEqual(m["m5"]["avg"], round((99 / 101 - 1) * 100, 2))
        self.assertAlmostEqual(m["tp"]["avg"], 5.0)          # 第 31 天最高 106.5 碰到 +5%（106.05）
        self.assertAlmostEqual(m["now"]["avg"], round((91 / 101 - 1) * 100, 2))
        self.assertEqual(m["mine"]["count"], 1)
        self.assertEqual(result["best"]["key"], "tp")
        with_fee = picker.perf(panel, params(**{**p, "fee": 1.0}))
        self.assertAlmostEqual({x["key"]: x for x in with_fee["methods"]}["d1"]["avg"], round((103 / 101 - 1) * 100 - 0.585, 2))


class SimulateTests(unittest.TestCase):
    def build(self) -> picker.Panel:
        dates = weekdays("2026-08-03", 20)       # 8/3（一）～8/28（五），四週
        data = {}
        for code, score in (("A", 15), ("B", 14), ("C", 13)):
            data[code] = [flat(100, score=score, hilen=30) for _ in dates]
        # 第二週：A 週二收黑、B 週三收黑、C 週三收黑
        data["A"][6] = flat(100, score=15, hilen=30, black=True)
        data["B"][7] = flat(100, score=14, hilen=30, black=True)
        data["C"][7] = flat(100, score=13, hilen=30, black=True)
        return make_panel(dates, data, bench=[50.0] * 19 + [55.0])

    def test_list_entries_full_and_benchmark(self) -> None:
        panel = self.build()
        p = params(sdays=0, weekN=3, maxpos=2, swaps=0, full=False, start=-1, fee=0, lots=1, fri=False)
        sim = picker.simulate(panel, p, len(panel.dates) - 1)
        self.assertTrue(sim["started"])
        self.assertEqual(sim["startDate"], "2026-08-10")
        buys = [(e["date"], a["code"]) for e in sim["log"][::-1] for a in e["actions"] if a["type"] == "buy"]
        self.assertEqual(buys, [("2026-08-11", "A"), ("2026-08-12", "B")])     # C 同一天收黑但滿檔了
        self.assertEqual(len(sim["holdings"]), 2)
        self.assertTrue(sim["full"])
        self.assertEqual(sim["account"]["quota"], 100.0)
        self.assertEqual(sim["account"]["bench"], 10.0)                       # 0050 從 50 漲到 55，同額度 100 萬賺 10 萬
        self.assertEqual(sim["account"]["invested"], 100.0)

    def test_full_swap_replaces_worst_loser(self) -> None:
        panel = self.build()
        panel.series["A"].c[7] = 90.0             # A 隔天跌到 90（賠錢）、但沒跌破 10 日線（日K不足 10 根算不出）
        p = params(sdays=0, weekN=3, maxpos=1, swaps=1, full=True, start=-1, fee=0, lots=1, fri=False, xma=20, reduce=False)
        sim = picker.simulate(panel, p, 7)
        day = sim["log"][0]
        self.assertEqual(day["date"], "2026-08-12")
        kinds = [(a["type"], a["code"]) for a in day["actions"]]
        self.assertEqual(kinds, [("exit", "A"), ("buy", "B")])                  # 滿檔換弱：賣掉賠錢的 A 換 B
        self.assertIn("滿檔換弱", day["actions"][0]["reason"])
        self.assertEqual(sim["swapsUsed"], 1)
        self.assertEqual([r["code"] for r in sim["missed"]], ["C"])             # 換股次數用完，C 沒進

    def test_friday_weed_out_and_norebuy(self) -> None:
        panel = self.build()
        for i in range(7, 10):
            panel.series["A"].c[i] = 98.0          # A 買了之後一直小賠
        panel.series["A"].o[10] = 99.0             # 下週一 A 又收黑（98 < 99），但已經不是同一週了
        p = params(sdays=0, weekN=1, maxpos=3, start=-1, fee=0, lots=1, fri=True, xma=20, reduce=False, swaps=0)
        sim = picker.simulate(panel, p, 9)
        exits = [(e["date"], a["reason"]) for e in sim["log"] for a in e["actions"] if a["type"] == "exit"]
        self.assertEqual(exits, [("2026-08-14", "週五汰弱・全部賣")])
        self.assertEqual(sim["holdings"], [])

    def test_start_next_week_is_not_started(self) -> None:
        panel = self.build()
        sim = picker.simulate(panel, params(sdays=0, start=0), len(panel.dates) - 1)
        self.assertFalse(sim["started"])
        self.assertEqual(sim["log"], [])
        self.assertTrue(sim["watch"])                                          # 下週名單照樣列出來看


class PicksTests(unittest.TestCase):
    def test_funnel_and_default_week(self) -> None:
        dates = weekdays("2026-08-03", 10)        # 最後一天 8/14 是星期五
        rows_a = [flat(100, hilen=1) for _ in dates]
        rows_a[9] = flat(110, hilen=30, black=True)    # 8/14 剛上榜
        rows_b = [flat(100, hilen=30) for _ in dates]   # 一直在池子裡
        panel = make_panel(dates, {"A": rows_a, "B": rows_b})
        out = picker.picks(panel, params(sdays=0, weekN=5), 9, None, None)
        self.assertEqual(out["funnel"], {"pool": 2, "week": 1, "daily": 1, "black": 0})
        self.assertEqual(out["week"]["start"], "2026-08-17")        # 星期五收盤：預設看下週名單
        self.assertTrue(out["week"]["next"])
        self.assertEqual([r["code"] for r in out["week"]["rows"]], ["A", "B"])   # 均線分、成交值都一樣：照代號
        self.assertEqual(out["daily"]["rows"][0]["code"], "A")
        self.assertEqual(out["pool"]["rows"][1]["since"], "2026-08-03")
        mid = picker.picks(panel, params(sdays=0, weekN=5), 7, None, None)
        self.assertEqual(mid["week"]["start"], "2026-08-10")        # 星期三：看這一週


class EndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        groups = {"半導體": [("6182", "合晶")]}
        self.patches = [patch.object(m, "STOCK_GROUPS", groups) for m in (heilong_backtest, brew_launch, brew_launch_history)]
        self.patches.append(patch.object(heilong_backtest, "HISTORY_DAYS", 60))
        self.patches.append(patch.object(heilong_backtest, "_warm_picker", lambda: None))
        for p in self.patches:
            p.start()
        brew_launch_history._group_by_code.clear()
        picker._panel = None
        picker._fp_cache["value"] = None
        dates = weekdays("2025-08-04", 300)
        rows = []
        price = 50.0
        for k, d in enumerate(dates):
            price = price * (1.012 if k % 7 else 0.985)
            o = price * (1.01 if k % 5 == 0 else 0.995)
            rows.append(("6182", d + "T00:00:00", round(o, 2), round(max(o, price) * 1.01, 2), round(min(o, price) * 0.99, 2), round(price, 2), 5000))
            rows.append(("0050", d + "T00:00:00", 100 + k * 0.1, 101 + k * 0.1, 99 + k * 0.1, 100 + k * 0.1, 9000))
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            c.execute("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES ('6182', '合晶', 'OTC', 'x')")
        import fundamentals_daily

        fundamentals_daily.save_shares("OTC", {"6182": 600_000_000})
        heilong_backtest.rebuild()

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        picker._panel = None
        for p in self.patches:
            p.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_views(self) -> None:
        client = TestClient(persistent_app.app)
        for view in ("today", "picks", "perf", "rules"):
            r = client.get("/api/hub/picker", params={"view": view, "sdays": 0, "mcap": 20, "val": 0.3, "score": 0, "start": -1})
            self.assertEqual(r.status_code, 200, r.text[:300])
            body = r.json()
            self.assertEqual(body["status"], "ok", body)
            self.assertEqual(body["data"]["days"], 60)
        today = client.get("/api/hub/picker", params={"view": "today", "sdays": 0, "mcap": 20, "val": 0.3, "score": 0, "start": -1}).json()
        self.assertIn("account", today["sim"])
        self.assertIsNotNone(today["sim"]["account"]["bench"])
        picks = client.get("/api/hub/picker", params={"view": "picks", "sdays": 0, "mcap": 20, "val": 0.3, "score": 0}).json()
        self.assertGreaterEqual(picks["funnel"]["pool"], 0)
        bad = client.get("/api/hub/picker", params={"view": "today", "score": 99})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(client.get("/api/hub/picker", params={"view": "nope"}).status_code, 422)
        # 表沒變：記憶體裡的特徵沿用同一份
        first = picker.load_panel()
        self.assertIs(picker.load_panel(), first)


if __name__ == "__main__":
    unittest.main()
