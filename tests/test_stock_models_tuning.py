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


ALWAYS = lambda day: True   # noqa: E731
NEVER = lambda day: False   # noqa: E731


class VariantConditionTests(unittest.TestCase):
    def test_gap_revenue_market(self) -> None:
        n = 40
        s = make("1111", [100 + k * 0.5 for k in range(n)])      # 離月線約 4%
        i = n - 1
        self.assertIsNotNone(sm.cond_big_inst(s, i))
        self.assertIsNotNone(tuning.variant_condition("bigInst", {"gap"}, ALWAYS)(s, i))
        slow = make("2222", [100 + k * 0.1 for k in range(n)])   # 站上月線但只高 1% → 貼著月線
        self.assertIsNotNone(sm.cond_big_inst(slow, i))
        self.assertIsNone(tuning.variant_condition("bigInst", {"gap"}, ALWAYS)(slow, i))

        self.assertIsNone(tuning.variant_condition("bigInst", {"revenue"}, ALWAYS)(s, i))   # 沒有營收資料
        s.revenue = {"2025-01": (100, "2025-02-10"), "2026-01": (120, "2026-02-10")}
        self.assertIsNotNone(tuning.variant_condition("bigInst", {"revenue"}, ALWAYS)(s, i))
        s.revenue = {"2025-01": (100, "2025-02-10"), "2026-01": (90, "2026-02-10")}
        self.assertIsNone(tuning.variant_condition("bigInst", {"revenue"}, ALWAYS)(s, i))

        self.assertIsNone(tuning.variant_condition("bigInst", {"market"}, NEVER)(s, i))
        self.assertIsNotNone(tuning.variant_condition("bigInst", {"market"}, ALWAYS)(s, i))

    def test_confirm_within_five_days(self) -> None:
        n = 40
        closes = [100.0] * n
        vols = [1000.0] * n
        closes[30] = 103.0     # 第 30 天：漲 3%、量 3 倍
        vols[30] = 3000.0
        s = make("1111", closes, vols=vols)
        for base_day, expect in ((30, True), (26, True), (25, False), (31, False)):
            fake = lambda ss, i, d=base_day: {"bigUp": 1.0, "week": "w"} if i == d else None   # noqa: E731
            with patch.dict(sm.CONDITIONS, {"bigInst": fake}):
                cond = tuning.variant_condition("bigInst", {"confirm"}, ALWAYS)
                got = cond(s, 30)
            self.assertEqual(bool(got), expect, base_day)
            if got:
                self.assertEqual(got["confirmPct"], 3.0)
        with patch.dict(sm.CONDITIONS, {"bigInst": lambda ss, i: {"bigUp": 1.0}}):
            self.assertIsNone(tuning.variant_condition("bigInst", {"confirm"}, ALWAYS)(s, 29))   # 沒漲沒量：不買

    def test_whale_low_day_trade(self) -> None:
        n = 40
        days = weekdays(date(2026, 1, 5), n)
        s = make("1111", [100 + k * 0.5 for k in range(n)], daytrade={d: 2000 for d in days}, daytrade_days=set(days))   # 當沖比 10%
        i = n - 1
        self.assertIsNone(sm.cond_whale_hot(s, i))
        self.assertIsNotNone(tuning.variant_condition("whaleHot", {"lowDt"}, ALWAYS)(s, i))
        s.daytrade = {d: 8000 for d in days}     # 40%
        self.assertIsNotNone(sm.cond_whale_hot(s, i))
        self.assertIsNone(tuning.variant_condition("whaleHot", {"lowDt"}, ALWAYS)(s, i))
        self.assertEqual([a["key"] for a in tuning.adjustments_for("bigInst")], ["top10", "confirm", "gap", "revenue", "market"])
        self.assertIn("lowDt", [a["key"] for a in tuning.adjustments_for("whaleHot")])
        self.assertNotIn("revenue", [a["key"] for a in tuning.adjustments_for("mainVsRetail")])   # 原條件就有
        self.assertNotIn("confirm", [a["key"] for a in tuning.adjustments_for("bigInstFire")])

    def test_market_filter(self) -> None:
        days = weekdays(date(2026, 1, 5), 30)
        up = tuning.market_filter({"TAIEX": [(d, 100 + k) for k, d in enumerate(days)]})
        down = tuning.market_filter({"TAIEX": [(d, 200 - k) for k, d in enumerate(days)]})
        self.assertFalse(up(days[10]))    # 不到 20 天沒有月線
        self.assertTrue(up(days[-1]))
        self.assertFalse(down(days[-1]))
        self.assertFalse(up("2030-01-01"))


class SelectTradesTests(unittest.TestCase):
    def test_weekly_top_picks_strongest_and_skips_held(self) -> None:
        n = 40
        series = {c: make(c, [100.0] * n) for c in ("A", "B", "C", "D")}
        signals = {
            "A": [(20, {"bigUp": 0.9, "week": "w1"})],
            "B": [(20, {"bigUp": 0.5, "week": "w1"})],
            "C": [(20, {"bigUp": 0.7, "week": "w1"}), (22, {"bigUp": 2.0, "week": "w1"})],   # 22 還抱著：跳過、不佔名額
            "D": [(23, {"bigUp": 0.1, "week": "w1"}), (27, {"bigUp": 0.1, "week": "w2"})],
        }
        picked = tuning.select_trades(series, signals, "bigUp", 2)
        self.assertEqual(sorted((t["code"], t["signal"]) for t in picked),
                         [("A", series["A"].dates[20]), ("C", series["C"].dates[20]), ("D", series["D"].dates[27])])
        everything = tuning.select_trades(series, signals, "bigUp", None)
        # 不限名額：A、B、C、D 第一個都買；C 第二個、D 第二個（23 買的抱到 33）都因為還抱著被跳過
        self.assertEqual(sorted((t["code"], t["signal"]) for t in everything),
                         [("A", series["A"].dates[20]), ("B", series["B"].dates[20]), ("C", series["C"].dates[20]), ("D", series["D"].dates[23])])


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

    def test_compute_tuning_end_to_end(self) -> None:
        days = self.seed()
        ctx = sm.prepare_inputs()
        out = tuning.compute_tuning(ctx)
        self.assertEqual(out["status"], "ok")
        self.assertEqual([m["key"] for m in out["models"]], tuning.CHIP_KEYS)
        self.assertGreater(out["window"]["from"], days[-140])     # 只比有集保的那段
        self.assertLess(out["window"]["from"], out["window"]["mid"])
        big = next(m for m in out["models"] if m["key"] == "bigInst")
        self.assertEqual([v["key"] for v in big["variants"]], ["base", "top10", "confirm", "gap", "revenue", "market", "all"])
        whale = next(m for m in out["models"] if m["key"] == "whaleHot")
        self.assertIn("lowDt", [v["key"] for v in whale["variants"]])
        base = big["variants"][0]
        self.assertGreater(base["all"]["trades"] + base["all"]["open"], 0)
        self.assertEqual(set(base), {"key", "label", "all", "a", "b"})
        # 前後兩段加起來就是整段（同一套交易，只是照發訊號的日子分）
        self.assertEqual(base["a"]["trades"] + base["b"]["trades"], base["all"]["trades"])
        rev = next(v for v in big["variants"] if v["key"] == "revenue")
        self.assertEqual(rev["all"]["trades"], 0)              # 沒有營收資料：多這個條件就一筆都沒有

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
