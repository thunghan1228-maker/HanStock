from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database
import stock_models as sm
import stock_models_tuning as tuning


def weekdays(start: date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def build(code: str, closes, *, opens=None, highs=None, lows=None, vols=None, start: date = date(2026, 1, 5), **kw) -> sm.StockSeries:
    n = len(closes)
    days = weekdays(start, n)
    opens = opens or [c * 1.005 for c in closes]
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    vols = vols or [3000.0] * n
    bars = [(d, o, h, l, c, v) for d, o, h, l, c, v in zip(days, opens, highs, lows, closes, vols)]
    inst = kw.pop("inst", {d: (-100_000, 0) for d in days})
    return sm.StockSeries(code, bars, inst, set(days), [], kw.pop("revenue", {}), **kw)


REVENUE = {**{f"2025-{m:02d}": (100, f"2025-{m + 1:02d}-10" if m < 12 else "2026-01-10") for m in range(1, 13)},
           "2026-01": (100, "2026-02-10"), "2026-02": (70, "2026-03-10")}   # 2 月年減 30%、一年新低


def down_series(next_day: tuple[float, float, float, float] | None = None) -> sm.StockSeries:
    """70 天一路跌（100 → 65.5），第 69 天是選股日；next_day＝隔天的 (開, 高, 低, 收)。"""
    closes = [100 - 0.5 * k for k in range(70)]
    opens = [c * 1.005 for c in closes]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    if next_day:
        o, h, l, c = next_day
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
    s = build("1111", closes, opens=opens, highs=highs, lows=lows, revenue=dict(REVENUE))
    s.cold_group = {d: "太陽能" for d in s.dates}
    s.cold_rank = {d: 0 for d in s.dates}
    return s


class ColdGroupTests(unittest.TestCase):
    def test_weakest_groups(self) -> None:
        series, groups = {}, {}
        for g in range(7):
            codes = []
            for m in range(3):
                code = f"{g}{m}00"
                series[code] = build(code, [100 + (g - 3) * 0.2 * k for k in range(40)])   # G0 跌最多、G6 漲最多
                codes.append(code)
            groups[f"G{g}"] = codes
        sm.attach_hot_groups(series, groups, top=5)
        last = series["0000"].dates[-1]
        self.assertEqual(series["0000"].cold_group[last], "G0")
        self.assertNotIn(last, series["6000"].cold_group)      # 最強的不會是最弱
        self.assertEqual(series["6000"].hot_group[last], "G6")
        self.assertNotIn(last, series["0000"].hot_group)


class SeriesHelpersTests(unittest.TestCase):
    def test_short_ratio_and_revenue_low(self) -> None:
        s = down_series()
        d = s.dates[69]
        self.assertIsNone(s.short_ratio(69))                   # 沒有資料：不判斷
        s.margin, s.margin_days = {d: 1000}, {d}
        s.short, s.short_days = {d: 250}, {d}
        self.assertEqual(s.short_ratio(69), 25.0)
        s.margin = {d: 0}
        self.assertEqual(s.short_ratio(69), 999.0)
        s.short = {d: 0}
        self.assertEqual(s.short_ratio(69), 0.0)
        self.assertTrue(s.revenue_low12("2026-02"))
        self.assertFalse(s.revenue_low12("2026-01"))


class ShortSignalTests(unittest.TestCase):
    def test_signal_and_filters(self) -> None:
        s = down_series()
        i = 69
        why = sm.cond_dt_short(s, i)
        self.assertIsNotNone(why)
        self.assertEqual(why["group"], "太陽能")
        self.assertEqual(why["yoy"], -30.0)
        self.assertEqual(why["shortBelow"], round(s.low[i], 2))
        self.assertEqual(why["reboundAt"], round(min(s.close[i], s.ma5[i]), 2))
        self.assertIsNone(why["shortRatio"])
        d = s.dates[i]
        s.margin, s.margin_days, s.short, s.short_days = {d: 1000}, {d}, {d: 400}, {d}
        self.assertIsNone(sm.cond_dt_short(s, i))              # 券資比 40% > 30%
        self.assertIsNotNone(sm.short_signal(s, i, max_ratio=None))
        s.short = {d: 100}
        self.assertEqual(sm.cond_dt_short(s, i)["shortRatio"], 10.0)
        s.cold_rank = {x: 5 for x in s.dates}                  # 第 6 弱：不在最弱 5 個
        self.assertIsNone(sm.cond_dt_short(s, i))
        s.cold_group, s.cold_rank = {}, {}
        self.assertIsNone(sm.cond_dt_short(s, i))
        self.assertIsNotNone(sm.short_signal(s, i, need_group=False))
        s.cold_group = {x: "太陽能" for x in s.dates}
        s.cold_rank = {x: 0 for x in s.dates}
        s.revenue = {**REVENUE, "2026-02": (110, "2026-03-10")}
        self.assertIsNone(sm.cond_dt_short(s, i))              # 營收年增：不空
        s.revenue = dict(REVENUE)
        s.inst = {x: (100_000, 0) for x in s.dates}
        self.assertIsNone(sm.cond_dt_short(s, i))              # 法人在買：不空
        up = build("2222", [50 + 0.5 * k for k in range(70)], revenue=dict(REVENUE))
        up.cold_group = {x: "太陽能" for x in up.dates}
        up.cold_rank = {x: 0 for x in up.dates}
        self.assertIsNone(sm.cond_dt_short(up, 69))            # 多頭排列：不空

    def test_level_signals_every_day(self) -> None:
        s = down_series()
        sig = sm.level_signals(s, sm.cond_dt_short, 60)
        self.assertEqual([i for i, _ in sig], list(range(60, 70)))


class SimulateDtShortTests(unittest.TestCase):
    def test_breakdown_profit_and_stop(self) -> None:
        prev_low = (100 - 0.5 * 69) * 0.99
        s = down_series((65.6, 66.0, 62.0, 63.0))              # 開在昨低上面、盤中跌破、收 63
        t = sm.simulate_dt_short(s, [(69, {})], entry="breakdown")[0]
        self.assertEqual((t["entry"], t["exit"], t["reason"], t["side"]), (round(prev_low, 2), 63.0, "close", "short"))
        self.assertAlmostEqual(t["ret"], round((prev_low - 63.0) / prev_low * 100 - sm.DAYTRADE_COST_PCT, 2))
        self.assertEqual(t["entryIdx"], t["exitIdx"])
        stopped = sm.simulate_dt_short(down_series((65.6, 68.0, 62.0, 63.0)), [(69, {})], entry="breakdown")[0]   # 盤中漲到空價 +4%
        self.assertEqual(stopped["reason"], "stop")
        self.assertAlmostEqual(stopped["ret"], round(-4.0 - sm.DAYTRADE_COST_PCT, 2))
        loose = sm.simulate_dt_short(down_series((65.6, 68.0, 62.0, 63.0)), [(69, {})], entry="breakdown", stop_pct=8.0)[0]
        self.assertEqual(loose["reason"], "close")

    def test_entries(self) -> None:
        prev_close = 100 - 0.5 * 69
        no_break = down_series((65.6, 66.0, 65.0, 65.5))      # 沒跌破昨低 64.85
        self.assertEqual(sm.simulate_dt_short(no_break, [(69, {})], entry="breakdown"), [])
        opened = sm.simulate_dt_short(no_break, [(69, {})], entry="open")[0]
        self.assertEqual(opened["entry"], 65.6)
        rebound = sm.simulate_dt_short(down_series((65.0, 65.6, 64.0, 64.2)), [(69, {})])[0]   # 正式版：反彈過不去再空
        self.assertEqual(rebound["entry"], round(prev_close, 2))   # 反彈到昨收（比 5 日線低）才空
        gap_up = down_series((66.0, 66.5, 64.0, 64.2))          # 開盤就在昨收上面：不空
        self.assertEqual(sm.simulate_dt_short(gap_up, [(69, {})], entry="rebound"), [])
        limit_down = down_series((prev_close * 0.9, prev_close * 0.9, prev_close * 0.9, prev_close * 0.9))
        self.assertEqual(sm.simulate_dt_short(limit_down, [(69, {})], entry="open"), [])
        self.assertEqual(sm.simulate_dt_short(down_series(), [(69, {})]), [])   # 沒有隔天


class ComputeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_compute_and_tuning_include_dt_short(self) -> None:
        days = weekdays(date(2025, 8, 1), 330)
        codes = ["1111", "2222", "3333"]
        with database.get_connection() as connection:
            connection.executemany("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, 'TSE', 'x')", [(c, c) for c in codes])
            rows = []
            for c in codes:
                for k, d in enumerate(days):
                    px = 200 - k * 0.3 - (2 if k % 9 < 5 else 0)
                    rows.append((c, d + "T00:00:00", px * 1.005, px * 1.01, px * 0.99, px, 8000))
            connection.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            sm._schema(connection)
            connection.executemany("INSERT INTO model_short_daily (trade_date, stock_code, balance) VALUES (?, '1111', 50)", [(d,) for d in days])
            connection.executemany("INSERT INTO model_margin_daily (trade_date, stock_code, balance) VALUES (?, '1111', 1000)", [(d,) for d in days])
        with patch.dict("stock_groups.STOCK_GROUPS", {"測試族群": [(c, c) for c in codes]}, clear=True):
            ctx = sm.prepare_inputs()
            out = sm.compute(ctx=ctx)
            tune = tuning.compute_tuning(ctx)
        s = ctx["series"]["1111"]
        self.assertEqual(s.short_ratio(len(s.dates) - 1), 5.0)
        self.assertEqual(s.cold_group.get(days[-1]), "測試族群")
        model = next(m for m in out["models"] if m["key"] == "dtShort")
        self.assertEqual((model["cat"], model["side"]), ("short", "short"))
        self.assertTrue(model["backtestRules"])
        self.assertNotIn("dtShort", s.hits)
        self.assertIn("trades", model)
        self.assertNotIn("trades", next(m for m in out["models"] if m["key"] == "bigInst"))   # 其他模型不給
        self.assertGreater(out["coverage"]["shortDays"], 0)
        self.assertEqual([v["key"] for v in tune["dtShort"]["variants"]], [v["key"] for v in tuning.DT_SHORT_VARIANTS])


if __name__ == "__main__":
    unittest.main()
