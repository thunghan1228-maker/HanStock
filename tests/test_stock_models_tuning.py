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


def make(code: str, closes: list[float], *, vols: list[float] | None = None, start: date = date(2026, 1, 5), **kw) -> sm.StockSeries:
    days = weekdays(start, len(closes))
    vols = vols or [20000.0] * len(closes)
    bars = [(d, c, c, c, c, v) for d, c, v in zip(days, closes, vols)]
    kw.setdefault("inst", {d: (100_000, 0) for d in days})
    kw.setdefault("tdcc", [("2026-02-13", 40.0, 20.0, 5000), ("2026-02-20", 40.6, 20.6, 4900)])
    kw.setdefault("revenue", {})
    return sm.StockSeries(code, bars, kw.pop("inst"), set(days), kw.pop("tdcc"), kw.pop("revenue"), **kw)


class MarketFilterTests(unittest.TestCase):
    def test_market_up_map(self) -> None:
        days = weekdays(date(2026, 1, 5), 30)
        up = sm.market_up_map({"TAIEX": [(d, 100 + k) for k, d in enumerate(days)]})
        down = sm.market_up_map({"TAIEX": [(d, 200 - k) for k, d in enumerate(days)]})
        self.assertFalse(up[days[10]])    # 不到 20 天沒有月線
        self.assertTrue(up[days[-1]])
        self.assertFalse(down[days[-1]])
        self.assertIsNone(sm.market_up_map({}))

    def test_chip_models_need_market_above_ma20(self) -> None:
        n = 40
        days = weekdays(date(2026, 1, 5), n)
        s = make("1111", [100 + k * 0.5 for k in range(n)], daytrade={d: 8000 for d in days}, daytrade_days=set(days))
        i = n - 1
        self.assertIsNotNone(sm.cond_big_inst(s, i))          # 沒給大盤資料：不判斷
        self.assertIsNotNone(sm.cond_whale_hot(s, i))
        s.market_up = {d: True for d in days}
        self.assertIsNotNone(sm.cond_big_inst(s, i))
        s.market_up = {d: d != days[-1] for d in days}         # 最後一天大盤跌破月線
        self.assertIsNone(sm.cond_big_inst(s, i))
        self.assertIsNone(sm.cond_whale_hot(s, i))
        s.market_up = {}                                       # 有給但那天沒有指數資料：不進場
        self.assertIsNone(sm.cond_big_inst(s, i))
        rules = next(m for m in sm.MODELS if m["key"] == "kiloBull")["rules"]
        self.assertTrue(any("加權指數" in r for r in rules))
        self.assertFalse(any("加權指數" in r for r in next(m for m in sm.MODELS if m["key"] == "instFirst")["rules"]))   # 比完沒效果的不加


class ExitRuleTests(unittest.TestCase):
    def test_stop_loss_and_hold_days(self) -> None:
        closes = [100.0] * 30 + [100, 95, 92, 97, 99, 101, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120]
        s = make("1111", closes)
        sig = [(29, {})]                                      # 第 30 根開盤 100 買
        base = sm.simulate(s, sig)[0]
        self.assertEqual(base["reason"], "ma20")              # 95 跌破月線
        self.assertEqual(base["exit"], 95.0)
        stop = sm.simulate(s, sig, ma20_exit=False, stop_pct=7.0)[0]
        self.assertEqual((stop["reason"], stop["exit"]), ("stop", 92.0))   # 92 跌 8%：停損
        loose = sm.simulate(s, sig, ma20_exit=False, stop_pct=10.0)[0]
        self.assertEqual((loose["reason"], loose["exitIdx"] - loose["entryIdx"] + 1), ("hold", 10))
        long = sm.simulate(s, sig, hold_days=20, ma20_exit=False, stop_pct=10.0)[0]
        self.assertEqual(long["exitIdx"] - long["entryIdx"] + 1, 20)
        self.assertEqual(long["exit"], 116.0)                  # 第 30 根買、第 49 根（第 20 天）收盤賣
        near_end = sm.simulate(s, [(len(closes) - 5, {})], hold_days=20, ma20_exit=False)[0]
        self.assertTrue(near_end["open"])                     # 還沒抱滿 20 天：持有中


class MarketComparisonTests(unittest.TestCase):
    def test_rev_boom_has_market_filter_and_comparison_toggles_it(self) -> None:
        n = 40
        closes = [100.0] * (n - 1) + [104.0]
        vols = [1000.0] * (n - 1) + [3000.0]
        rev = {f"2025-{m:02d}": (100, f"2025-{m:02d}-10") for m in range(1, 13)}
        rev["2026-01"] = (150, "2026-02-10")                   # 年增 50%、一年新高
        days = weekdays(date(2026, 1, 5), n)
        s = make("1111", closes, vols=vols, revenue=rev)
        i = n - 1
        self.assertIsNotNone(sm.cond_rev_boom(s, i))           # 沒給大盤資料：不判斷
        s.market_up = {d: False for d in days}
        self.assertIsNone(sm.cond_rev_boom(s, i))              # 正式版：大盤在月線下不進場
        self.assertIsNotNone(sm.rev_boom_core(s, i))
        self.assertIsNotNone(tuning.market_condition("revBoom", False)(s, i))
        self.assertIsNone(tuning.market_condition("revBoom", True)(s, i))
        s.market_up = {d: True for d in days}
        self.assertIsNotNone(tuning.market_condition("revBoom", True)(s, i))
        self.assertTrue(any("加權指數" in r for r in next(m for m in sm.MODELS if m["key"] == "revBoom")["rules"]))

    def test_other_models_toggle(self) -> None:
        calls = []

        def fake(s, i):
            calls.append(i)
            return {"x": 1}

        n = 30
        days = weekdays(date(2026, 1, 5), n)
        s = make("1111", [100.0] * n)
        s.market_up = {d: k % 2 == 0 for k, d in enumerate(days)}
        with patch.dict(sm.CONDITIONS, {"instFirst": fake}):
            off = tuning.market_condition("instFirst", False)
            on = tuning.market_condition("instFirst", True)
            self.assertIsNotNone(off(s, 1))
            self.assertIsNone(on(s, 1))                        # 那天大盤在月線下
            self.assertIsNotNone(on(s, 2))

    def test_trust_growth_and_inst_then_retail_filtered(self) -> None:
        self.assertEqual(set(sm.CORE_CONDITIONS), {"revBoom", "trustGrowth", "instThenRetail"})
        n = 30
        days = weekdays(date(2026, 1, 5), n)
        s = make("1111", [100.0] * n)
        for key, core in (("trustGrowth", "trust_growth_core"), ("instThenRetail", "inst_then_retail_core")):
            with patch.object(sm, core, lambda ss, i: {"x": 1}):
                s.market_up = {d: True for d in days}
                self.assertIsNotNone(sm.CONDITIONS[key](s, 5))
                s.market_up = {d: False for d in days}
                self.assertIsNone(sm.CONDITIONS[key](s, 5))     # 正式版：大盤在月線下不進場
            rules = next(m for m in sm.MODELS if m["key"] == key)["rules"]
            self.assertTrue(any("加權指數" in r for r in rules))


class ComputeTuningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        sm._state.update({"running": False, "computedKey": None})

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def seed(self) -> list[str]:
        import fundamentals_daily

        days = weekdays(date(2025, 8, 1), 330)
        with database.get_connection() as connection:
            connection.executemany("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, 'TSE', 'x')", [("1111", "甲")])
            rows = []
            for k, d in enumerate(days):
                c1 = 50 + k * 0.1 + (3 if k % 15 < 7 else 0)
                rows.append(("1111", d + "T00:00:00", c1, c1 + 1, c1 - 1, c1, 5000))
            connection.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            sm._schema(connection)
            connection.executemany("INSERT INTO model_inst_daily (trade_date, stock_code, foreign_net, trust_net, dealer_net) VALUES (?, ?, ?, ?, 0)",
                                   [(d, "1111", 300_000, 100_000) for d in days])
            connection.executemany("INSERT INTO model_index_daily (index_id, trade_date, close) VALUES (?, ?, ?)",
                                   [(ix, d, 100 + k) for ix in ("TAIEX", "TPEx") for k, d in enumerate(days)])
            fundamentals_daily._schema(connection)
            fridays = [d for d in days if date.fromisoformat(d).weekday() == 4][-26:]   # 跟正式環境一樣只有最近約半年的集保
            tdcc = []
            for w, d in enumerate(fridays):
                tdcc += [(d, "1111", 12, 10, 1000, 10 + w * 0.6), (d, "1111", 15, 10, 1000, 20 + w * 0.6), (d, "1111", 17, 5000 - w * 60, 100000, 100.0)]
            connection.executemany("INSERT INTO tdcc_weekly (data_date, stock_code, level, holders, shares, pct) VALUES (?, ?, ?, ?, ?, ?)", tdcc)
        return days

    def test_prepare_inputs_attaches_market(self) -> None:
        days = self.seed()
        ctx = sm.prepare_inputs()
        s = ctx["series"]["1111"]
        self.assertTrue(s.market_up[days[-1]])
        self.assertTrue(s.market_ok(len(s.dates) - 1))

    def test_compute_tuning_end_to_end(self) -> None:
        self.seed()
        ctx = sm.prepare_inputs()
        sm.compute(ctx=ctx)                        # 共振要先有其他模型的訊號
        out = tuning.compute_tuning(ctx)
        self.assertEqual(out["status"], "ok")
        self.assertEqual([m["key"] for m in out["exits"]], [m["key"] for m in sm.MODELS])
        big = next(m for m in out["exits"] if m["key"] == "bigInst")
        inst = next(m for m in out["exits"] if m["key"] == "instThenRetail")
        self.assertGreater(big["window"]["from"], inst["window"]["from"])     # 用到集保的只比有集保的那段
        self.assertEqual(inst["window"]["from"], ctx["bt_days"][0])
        self.assertEqual([v["key"] for v in big["variants"]], [r["key"] for r in tuning.EXIT_RULES])
        base = big["variants"][0]
        self.assertGreater(base["all"]["trades"] + base["all"]["open"], 0)
        self.assertEqual(base["a"]["trades"] + base["b"]["trades"], base["all"]["trades"])
        self.assertEqual(set(base), {"key", "label", "all", "a", "b"})
        self.assertIn("avgDays", base["all"])
        self.assertEqual([m["key"] for m in out["market"]], tuning.MARKET_KEYS)
        rev = out["market"][0]
        self.assertEqual({m["key"]: m["current"] for m in out["market"]},
                         {"revBoom": "on", "trustGrowth": "on", "instThenRetail": "on", "instFirst": "off", "multiCombo": "off"})
        self.assertEqual([v["key"] for v in rev["variants"]], ["off", "on"])
        self.assertEqual(rev["window"]["from"], ctx["bt_days"][0])    # 整年
        for m in out["market"]:
            off, on = m["variants"]
            self.assertGreaterEqual(off["all"]["trades"] + off["all"]["open"], 0)
            self.assertEqual(off["a"]["trades"] + off["b"]["trades"], off["all"]["trades"])

    def test_run_once_saves_tuning(self) -> None:
        self.seed()

        def boom(params):
            raise RuntimeError("HTTP 400: nope")

        with patch.object(sm, "_finmind", boom):
            self.assertEqual(sm.run_once()["status"], "ok")
        got = sm.cached("tuning")
        self.assertEqual(got["status"], "ok")
        self.assertEqual(sm.cached()["asOf"], got["asOf"])
        self.assertIsNone(sm._state.get("tuningError"))

    def test_tuning_error_does_not_break_models(self) -> None:
        self.seed()

        def boom(params):
            raise RuntimeError("HTTP 400: nope")

        with patch.object(sm, "_finmind", boom), patch("stock_models_tuning.compute_tuning", side_effect=ValueError("bad")):
            self.assertEqual(sm.run_once()["status"], "ok")
        self.assertIsNotNone(sm.cached())
        self.assertIsNone(sm.cached("tuning"))
        self.assertIn("bad", sm._state["tuningError"])


if __name__ == "__main__":
    unittest.main()
