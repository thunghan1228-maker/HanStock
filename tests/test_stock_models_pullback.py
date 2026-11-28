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
    opens = opens or [c * 0.995 for c in closes]
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    vols = vols or [3000.0] * n
    bars = [(d, o, h, l, c, v) for d, o, h, l, c, v in zip(days, opens, highs, lows, closes, vols)]
    inst = kw.pop("inst", {d: (100_000, 0) for d in days})
    return sm.StockSeries(code, bars, inst, set(days), [], kw.pop("revenue", {}), **kw)


REVENUE = {**{f"2025-{m:02d}": (100, f"2025-{m + 1:02d}-10" if m < 12 else "2026-01-10") for m in range(1, 13)},
           "2026-01": (100, "2026-02-10"), "2026-02": (150, "2026-03-10")}   # 2 月年增 50%


def setup_series(extra_after: list[tuple[float, float, float, float]] | None = None) -> sm.StockSeries:
    """70 天一路漲（50 → 84.5），第 70 天拉回量縮（設定日），之後照 extra_after 的 (開, 高, 低, 收)。"""
    closes = [50 + 0.5 * k for k in range(70)] + [81.5]
    opens = [c * 0.995 for c in closes]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    vols = [3000.0] * 70 + [1500.0]
    for o, h, l, c in extra_after or []:
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        vols.append(3000.0)
    s = build("1111", closes, opens=opens, highs=highs, lows=lows, vols=vols, revenue=dict(REVENUE))
    s.shares = 100_000_000
    s.hot_group = {d: "記憶體" for d in s.dates}
    s.hot_rank = {d: 0 for d in s.dates}
    return s


class HotGroupTests(unittest.TestCase):
    def test_top_groups_by_5_and_20_day_strength(self) -> None:
        series, groups = {}, {}
        for g in range(6):
            codes = []
            for m in range(3):
                code = f"{g}{m}00"
                series[code] = build(code, [50 + (g + 1) * 0.1 * k for k in range(40)])
                codes.append(code)
            groups[f"G{g}"] = codes
        groups["小"] = ["0000", "0100"]                 # 不到 3 檔有資料：不排
        sm.attach_hot_groups(series, groups, top=5)
        last = series["5000"].dates[-1]
        self.assertEqual(series["5000"].hot_group[last], "G5")
        self.assertNotIn(last, series["0000"].hot_group)  # G0 最弱，第 6 名；「小」不排
        self.assertEqual(series["1000"].hot_group[last], "G1")
        self.assertNotIn(series["5000"].dates[10], series["5000"].hot_group)   # 前 20 天算不出來
        self.assertEqual(series["5000"].hot_rank[last], 0)
        self.assertEqual(series["1000"].hot_rank[last], 4)


class SetupTests(unittest.TestCase):
    def test_pullback_signal(self) -> None:
        s = setup_series()
        i = 70
        self.assertTrue(sm.pullback_setup(s, i))
        why = sm.group_pullback_signal(s, i)
        self.assertIsNotNone(why)
        self.assertEqual(why["group"], "記憶體")
        self.assertEqual(why["buyAbove"], round(81.5 * 1.01, 2))
        self.assertEqual(why["stopAt"], round(81.5 * 0.99, 2))     # 拉回低點比跌 8% 近
        self.assertEqual(why["yoy"], 50.0)
        self.assertFalse(sm.pullback_setup(s, 69))                # 還在漲，不是拉回
        self.assertIsNone(sm.cond_group_pullback(s, 69))

    def test_filters(self) -> None:
        s = setup_series()
        i = 70
        s.market_up = {d: False for d in s.dates}
        self.assertIsNone(sm.cond_group_pullback(s, i))          # 大盤在月線下
        self.assertIsNotNone(sm.group_pullback_signal(s, i, market=False))
        s.market_up = None
        s.shares = 600_000_000                                    # 股本 60 億：不是中小型（≤ 50 億）
        self.assertIsNone(sm.cond_group_pullback(s, i))
        self.assertIsNotNone(sm.group_pullback_signal(s, i, max_shares=1_000_000_000))   # 比較用的放寬版（100 億）算
        s.shares = None
        self.assertIsNone(sm.cond_group_pullback(s, i))
        s.shares = 100_000_000
        s.hot_rank = {d: 6 for d in s.dates}                    # 第 7 名：不在最強 5 個
        self.assertIsNone(sm.cond_group_pullback(s, i))
        self.assertIsNotNone(sm.group_pullback_signal(s, i, group_top=8))   # 比較用的放寬版（8 個）算
        s.hot_group, s.hot_rank = {}, {}
        self.assertIsNone(sm.cond_group_pullback(s, i))          # 不在最強族群
        self.assertIsNotNone(sm.group_pullback_signal(s, i, need_group=False))
        s.hot_group = {d: "記憶體" for d in s.dates}
        s.hot_rank = {d: 0 for d in s.dates}
        s.revenue = {**REVENUE, "2026-02": (110, "2026-03-10")}  # 年增 10%、也不是一年新高（1 月 100、去年都 100）
        self.assertIsNotNone(sm.cond_group_pullback(s, i))       # 110 > 前 11 個月 → 一年新高，照樣算
        s.revenue = {**REVENUE, "2026-02": (90, "2026-03-10")}
        self.assertIsNone(sm.cond_group_pullback(s, i))
        s.revenue = dict(REVENUE)
        s.inst = {d: (-100_000, 0) for d in s.dates}
        self.assertIsNone(sm.cond_group_pullback(s, i))

    def test_breakout_setup(self) -> None:
        closes = [100.0] * 30 + [103.0]
        vols = [1000.0] * 30 + [2000.0]
        s = build("1111", closes, vols=vols)
        self.assertTrue(sm.breakout_setup(s, 30))
        self.assertFalse(sm.breakout_setup(s, 29))


class SimulatePullbackTests(unittest.TestCase):
    def test_target_then_stop_vs_fixed(self) -> None:
        s = setup_series([(81.6, 86.0, 81.0, 85.0), (85.0, 93.0, 84.0, 92.0), (92.0, 93.0, 86.0, 86.0), (85.0, 85.5, 79.0, 80.0), (80, 81, 79, 80)])
        sig = [(70, {})]
        price = 81.5 * 1.01                                       # 第 71 天突破設定日高點買
        t = sm.simulate_pullback(s, sig)[0]
        self.assertEqual(t["entry"], round(price, 2))
        stop = 81.5 * 0.99
        self.assertEqual((t["reason"], t["exitIdx"], t["exit"]), ("stop+half", 74, round(stop, 2)))
        half = price * 1.12
        self.assertAlmostEqual(t["ret"], round(((half / price - 1) * 100 + (stop / price - 1) * 100) / 2 - sm.COST_PCT, 2))
        f = sm.simulate_pullback(s, sig, stop_mode="fixed")[0]   # 停損只看 8%（75.7）：第 74 天沒碰到，收盤跌破 5 日線賣
        self.assertEqual((f["reason"], f["exitIdx"], f["exit"]), ("ma5+half", 74, 80.0))

    def test_no_trigger_no_trade(self) -> None:
        s = setup_series([(81.0, 82.0, 80.5, 81.8)])              # 高 82 沒過設定日高 82.3
        self.assertEqual(sm.simulate_pullback(s, [(70, {})]), [])
        opened = sm.simulate_pullback(s, [(70, {})], entry="open")
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["entry"], 81.0)
        self.assertTrue(opened[0]["open"])                       # 資料到這天為止：持有中

    def test_hold_days_limit(self) -> None:
        rising = [(83.0 + k, 84.5 + k, 82.5 + k, 84.0 + k) for k in range(6)]   # 一路小漲、沒到 +12%
        s = setup_series(rising)
        five = sm.simulate_pullback(s, [(70, {})])[0]
        self.assertEqual((five["reason"], five["exitIdx"]), ("hold", 75))
        three = sm.simulate_pullback(s, [(70, {})], hold_days=3)[0]
        self.assertEqual((three["reason"], three["exitIdx"]), ("hold", 73))

    def test_entry_day_uses_close_for_stop(self) -> None:
        s = setup_series([(81.6, 83.0, 79.0, 82.0), (82.0, 83.0, 81.0, 82.5)])   # 買進當天盤中破 80.7、收 82 沒破
        t = sm.simulate_pullback(s, [(70, {})])[0]
        self.assertNotEqual(t["exitIdx"], 71)


class ComputeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_compute_and_tuning_include_group_pullback(self) -> None:
        import fundamentals_daily

        days = weekdays(date(2025, 8, 1), 330)
        codes = ["1111", "2222", "3333"]
        with database.get_connection() as connection:
            connection.executemany("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, 'TSE', 'x')", [(c, c) for c in codes])
            rows = []
            for c in codes:
                for k, d in enumerate(days):
                    px = 50 + k * 0.15 + (2 if k % 9 < 5 else 0)
                    rows.append((c, d + "T00:00:00", px, px * 1.01, px * 0.99, px, 8000))
            connection.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            sm._schema(connection)
            connection.executemany("INSERT INTO model_index_daily (index_id, trade_date, close) VALUES (?, ?, ?)",
                                   [(ix, d, 100 + k) for ix in ("TAIEX", "TPEx") for k, d in enumerate(days)])
            fundamentals_daily._schema(connection)
            connection.executemany("INSERT INTO stock_shares (stock_code, shares, market, updated_at) VALUES (?, 100000000, 'TSE', 'x')", [(c,) for c in codes])
            connection.executemany("INSERT INTO model_inst_daily (trade_date, stock_code, foreign_net, trust_net, dealer_net) VALUES (?, ?, 200000, 0, 0)",
                                   [(d, c) for c in codes for d in days])
            revenue = []
            for c in codes:                                       # 每個月營收年增 50%
                for y in (2024, 2025, 2026):
                    for m in range(1, 13):
                        rev = 100 * (1.5 ** (y - 2024))
                        revenue.append((c, f"{y}-{m:02d}", int(rev), f"{y + (m == 12)}-{m % 12 + 1:02d}-10"))
            connection.executemany("INSERT INTO model_revenue (stock_code, ym, revenue, announced) VALUES (?, ?, ?, ?)", revenue)
        with patch.dict("stock_groups.STOCK_GROUPS", {"測試族群": [(c, c) for c in codes]}, clear=True):
            ctx = sm.prepare_inputs()
            out = sm.compute(ctx=ctx)
            tune = tuning.compute_tuning(ctx)
        s = ctx["series"]["1111"]
        self.assertEqual(s.shares, 100_000_000)
        self.assertEqual(s.hot_group.get(days[-1]), "測試族群")
        model = next(m for m in out["models"] if m["key"] == "groupPullback")
        self.assertEqual(model["cat"], "short")
        self.assertIn("watch", model)
        # 三檔一模一樣：今天剛好拉回到位就在觀察名單，不然就在等拉回名單
        self.assertEqual(len(model["watch"]) + len(model["candidates"]), 3)
        for c in model["candidates"]:
            self.assertEqual(c["why"]["group"], "測試族群")
            self.assertAlmostEqual(c["why"]["waitAt"], round(c["why"]["ma5"] * 1.02, 2))
        self.assertTrue(model["backtestRules"])
        self.assertNotIn("groupPullback", s.hits)                 # 不算進多模型共振
        self.assertEqual([v["key"] for v in tune["pullback"]["variants"]], [v["key"] for v in tuning.PULLBACK_VARIANTS])
        self.assertEqual(tune["pullback"]["window"]["from"], ctx["bt_days"][0])


if __name__ == "__main__":
    unittest.main()
