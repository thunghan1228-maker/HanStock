from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database
import stock_models as module


def weekdays(start: date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def series(closes: list[float], *, vols: list[float] | None = None, opens: list[float] | None = None, start: date = date(2026, 1, 5),
           inst: dict | None = None, tdcc: list | None = None, revenue: dict | None = None, **kw) -> module.StockSeries:
    days = weekdays(start, len(closes))
    vols = vols or [1000.0] * len(closes)
    opens = opens or closes
    bars = [(d, o, max(o, c), min(o, c), c, v) for d, o, c, v in zip(days, opens, closes, vols)]
    inst = inst if inst is not None else {}
    return module.StockSeries("1234", bars, inst, set(days), tdcc or [], revenue or {}, **kw)


class ParserTests(unittest.TestCase):
    def test_institutional_sums_by_investor(self) -> None:
        payload = {"data": [
            {"date": "2026-11-24", "stock_id": "2330", "buy": 4970501, "name": "Foreign_Investor", "sell": 9068889},
            {"date": "2026-11-24", "stock_id": "2330", "buy": 10, "name": "Foreign_Dealer_Self", "sell": 0},
            {"date": "2026-11-24", "stock_id": "2330", "buy": 1039000, "name": "Investment_Trust", "sell": 121168},
            {"date": "2026-11-24", "stock_id": "2330", "buy": 170000, "name": "Dealer_self", "sell": 84000},
            {"date": "2026-11-24", "stock_id": "2330", "buy": 313808, "name": "Dealer_Hedging", "sell": 11340},
        ]}
        self.assertEqual(module.parse_institutional(payload), {"2026-11-24": {"2330": (4970511 - 9068889, 1039000 - 121168, 86000 + 302468)}})

    def test_margin_daytrade_revenue_index(self) -> None:
        self.assertEqual(module.parse_margin({"data": [{"date": "2026-11-25", "stock_id": "2330", "MarginPurchaseTodayBalance": 29305}]}), {"2026-11-25": {"2330": 29305}})
        self.assertEqual(module.parse_daytrade({"data": [{"stock_id": "2330", "date": "2026-11-25", "Volume": 20112000}]}), {"2026-11-25": {"2330": 20112}})
        rows = module.parse_revenue({"data": [{"date": "2026-11-01", "stock_id": "2330", "revenue": 586890319000, "revenue_month": 10, "revenue_year": 2026, "create_time": "2026-11-10"}]})
        self.assertEqual(rows, [("2330", "2026-10", 586890319000, "2026-11-10")])
        self.assertEqual(module.parse_index({"data": [{"date": "2026-11-24", "close": 40122.35}]}), {"2026-11-24": 40122.35})
        self.assertEqual(module.parse_institutional({"msg": "error", "status": 400}), {})


class RevenueTests(unittest.TestCase):
    def test_only_uses_announced_months(self) -> None:
        rev = {"2025-09": (100, "2025-10-09"), "2025-10": (100, "2025-11-10"), "2026-09": (130, "2026-10-08"), "2026-10": (160, "2026-11-10")}
        # 11/09 還沒公布 10 月營收：看到的是 9 月（年增 30%），上個月（8 月）沒有去年資料
        got0 = module.revenue_yoy_at(rev, "2026-11-09")
        self.assertEqual((got0[0], got0[2]), ("2026-09", None))
        self.assertAlmostEqual(got0[1], 30.0)
        got = module.revenue_yoy_at(rev, "2026-11-10")
        self.assertEqual(got[0], "2026-10")
        self.assertAlmostEqual(got[1], 60.0)
        self.assertAlmostEqual(got[2], 30.0)
        # 沒有公布日：當成營收月份隔月 10 號
        self.assertEqual(module.revenue_yoy_at({"2025-10": (100, None), "2026-10": (120, None)}, "2026-11-09"), None)
        self.assertEqual(module.revenue_yoy_at({"2025-10": (100, None), "2026-10": (120, None)}, "2026-11-10")[0], "2026-10")


class ConditionTests(unittest.TestCase):
    def test_kilo_bull(self) -> None:
        closes = [100 + i * 0.2 for i in range(80)]
        closes[-1] = closes[-2] + 3            # 創 20 日新高
        vols = [1000.0] * 75 + [3000.0] * 5    # 5 日均量 3 倍
        s = series(closes, vols=vols, tdcc=[("2026-03-27", 40.0, 20.0, 5000), ("2026-04-03", 40.2, 20.2, 4950), ("2026-04-10", 40.5, 20.5, 4900)])
        i = len(closes) - 1
        self.assertGreater(s.dates[i], "2026-04-10")
        got = module.cond_kilo_bull(s, i)
        self.assertIsNotNone(got)
        self.assertEqual(got["kiloUp"], 0.5)
        # 千張大戶只增加一週 → 不成立
        s2 = series(closes, vols=vols, tdcc=[("2026-03-27", 40.0, 20.6, 5000), ("2026-04-03", 40.2, 20.2, 4950), ("2026-04-10", 40.5, 20.5, 4900)])
        self.assertIsNone(module.cond_kilo_bull(s2, i))

    def test_big_inst_needs_inst_coverage(self) -> None:
        closes = [100 + i * 0.3 for i in range(40)]
        days = weekdays(date(2026, 1, 5), 40)
        inst = {d: (5000, 2000) for d in days}
        tdcc = [("2026-02-13", 40.0, 20.0, 5000), ("2026-02-20", 40.6, 20.0, 5000)]
        s = series(closes, inst=inst, tdcc=tdcc, vols=[20000.0] * 40)
        i = len(closes) - 1
        self.assertIsNotNone(module.cond_big_inst(s, i))
        s.inst_days = set(days[:-1])    # 最後一天整個市場沒有法人資料 → 不判斷
        self.assertIsNone(module.cond_big_inst(s, i))

    def test_inst_first_and_day_trade(self) -> None:
        n = 40
        closes = [100 + i * 0.2 for i in range(n)]
        days = weekdays(date(2026, 1, 5), n)
        inst = {d: (0, -1000) for d in days}
        inst[days[-1]] = (0, 80_000)          # 投信 80 張＝當天成交量 2000 張的 4%，不到 5%
        dt = {d: 200 for d in days}           # 當沖比 10%
        s = series(closes, inst=inst, vols=[2000.0] * n, daytrade=dt, daytrade_days=set(days))
        self.assertIsNone(module.cond_inst_first(s, n - 1))
        inst[days[-1]] = (0, 600_000)         # 600 張＝30%
        self.assertEqual(module.cond_inst_first(s, n - 1)["who"], "投信")
        s.daytrade = {d: 600 for d in days}   # 當沖比 30% → 熱度已起來
        self.assertIsNone(module.cond_inst_first(s, n - 1))


class BacktestTests(unittest.TestCase):
    def test_simulate_rules(self) -> None:
        closes = [100.0] * 30 + [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112]
        opens = list(closes)
        opens[31] = 100.5
        s = series(closes, opens=opens)
        trades = module.simulate(s, [(30, {}), (32, {})])   # 第二個訊號在持有中 → 不重複買
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t["entryDate"], t["entry"]), (s.dates[31], 100.5))
        self.assertEqual(t["exitDate"], s.dates[31 + module.HOLD_DAYS - 1])
        self.assertAlmostEqual(t["ret"], round((s.close[40] / 100.5 - 1) * 100 - module.COST_PCT, 2))
        self.assertFalse(t["open"])
        # 開盤就漲停：跳過
        opens2 = list(closes)
        opens2[31] = 111.0                    # ≥ 101 × 1.095
        self.assertEqual(module.simulate(series(closes, opens=opens2), [(30, {})]), [])
        # 跌破月線：當天收盤出
        closes3 = [100.0] * 30 + [101, 90, 95]
        t3 = module.simulate(series(closes3), [(30, {})])[0]
        self.assertEqual((t3["exitDate"], t3["reason"], t3["open"]), (series(closes3).dates[31], "ma20", False))
        # 還沒到出場日：持有中
        t4 = module.simulate(series([100.0] * 30 + [101, 102, 103]), [(30, {})])[0]
        self.assertTrue(t4["open"])

    def test_signal_edge_only(self) -> None:
        s = series([100.0] * 10)
        flags = [False, True, True, False, True, True, True, False, False, True]
        got = module.signals_for(s, lambda _s, i: {"x": 1} if flags[i] else None, 1)
        self.assertEqual([i for i, _ in got], [1, 4, 9])

    def test_summarize_capital_and_return(self) -> None:
        closes = [100.0] * 30 + [100, 110, 110, 110, 110, 110, 110, 110, 110, 110, 110, 110]
        s = series(closes)
        trades = module.simulate(s, [(29, {})])   # 隔天（第 30 根）開盤 100 買
        stats = module.summarize(trades, {"1234": s}, s.dates[25:], {})
        self.assertEqual(stats["maxConcurrent"], 1)
        self.assertEqual(stats["maxCapital"], module.POSITION_NTD)
        self.assertEqual(stats["trades"], 1)
        self.assertAlmostEqual(stats["retOnCapital"], round(10 - module.COST_PCT, 1))
        self.assertEqual(stats["winRate"], 100.0)


class ComputeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_compute_end_to_end(self) -> None:
        import fundamentals_daily

        days = weekdays(date(2025, 8, 1), 330)
        with database.get_connection() as connection:
            connection.executemany("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, 'TSE', 'x')", [("1111", "甲"), ("2222", "乙")])
            rows = []
            for k, d in enumerate(days):
                c1 = 50 + k * 0.1 + (3 if k % 15 < 7 else 0)   # 一直往上、每 15 天上下一次（條件會反覆成立）
                rows.append(("1111", d + "T00:00:00", c1, c1 + 1, c1 - 1, c1, 5000))
                rows.append(("2222", d + "T00:00:00", 30.0, 30.5, 29.5, 30.0, 100))   # 成交值太小，不會入選
            connection.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            module._schema(connection)
            connection.executemany("INSERT INTO model_inst_daily (trade_date, stock_code, foreign_net, trust_net, dealer_net) VALUES (?, ?, ?, ?, 0)",
                                   [(d, "1111", 300_000, 100_000) for d in days])
            connection.executemany("INSERT INTO model_index_daily (index_id, trade_date, close) VALUES (?, ?, ?)",
                                   [(ix, d, 100 + k) for ix in ("TAIEX", "TPEx") for k, d in enumerate(days)])
        fridays = [d for d in days if date.fromisoformat(d).weekday() == 4]
        with database.get_connection() as connection:
            fundamentals_daily._schema(connection)
            rows = []
            for w, d in enumerate(fridays):   # 大戶每週 +0.6、千張每週 +0.2、總人數每週少 60
                rows += [(d, "1111", 12, 10, 1000, 10 + w * 0.6), (d, "1111", 15, 10, 1000, 20 + w * 0.2), (d, "1111", 17, 5000 - w * 60, 100000, 100.0)]
            connection.executemany("INSERT INTO tdcc_weekly (data_date, stock_code, level, holders, shares, pct) VALUES (?, ?, ?, ?, ?, ?)", rows)
        out = module.compute()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["asOf"], days[-1])
        self.assertEqual([m["key"] for m in out["models"]], [m["key"] for m in module.MODELS])
        self.assertEqual(len(out["models"]), 10)
        big = next(m for m in out["models"] if m["key"] == "bigInst")
        self.assertGreater(big["stats"]["trades"] + big["stats"]["open"], 0)
        self.assertTrue(all(t["code"] == "1111" for t in big["lastTrades"] + big["holding"]))
        self.assertTrue(out["index"]["taiex"])
        self.assertNotIn("taiex", big["stats"])
        self.assertEqual(out["coverage"]["stocks"], 2)

    def test_backfill_logs_and_skips(self) -> None:
        calls = []

        def fetch(params):
            calls.append(params)
            if params["dataset"] == "TaiwanStockInstitutionalInvestorsBuySell":
                d = params["start_date"]
                return {"data": [{"date": d, "stock_id": "1111", "buy": 2000, "sell": 500, "name": "Investment_Trust"}]}
            return {"data": []}

        days = ["2026-11-20", "2026-11-19"]
        with patch.object(module, "_now", return_value=module.datetime(2026, 11, 26, 20, 0, tzinfo=module.TW_TZ)):
            first = module.backfill(days, fetch=fetch)
            n1 = len(calls)
            second = module.backfill(days, fetch=fetch)
        self.assertEqual(first["inst"], 2)
        self.assertEqual(second["inst"], 0)          # 抓到資料的不再抓
        self.assertLess(len(calls) - n1, n1)          # 第二次只重試空的（margin／daytrade／營收），而且舊的一天只試一次
        with database.get_connection() as connection:
            got = connection.execute("SELECT trust_net FROM model_inst_daily WHERE trade_date = '2026-11-20' AND stock_code = '1111'").fetchone()
        self.assertEqual(got["trust_net"], 1500)


if __name__ == "__main__":
    unittest.main()


class DailyExtrasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_official_overrides_finmind(self) -> None:
        import chips_daily
        import margin_balance

        with database.get_connection() as connection:
            module._schema(connection)
            chips_daily._schema(connection)
            margin_balance._schema(connection)
            connection.executemany("INSERT INTO model_inst_daily VALUES (?, '2344', ?, ?, 0)", [("2026-11-25", 1_000_000, 500_000), ("2026-11-26", 1, 1)])
            connection.executemany("INSERT INTO model_margin_daily VALUES (?, '2344', ?)", [("2026-11-25", 30000), ("2026-11-26", 1)])
        chips_daily.save_institutional("2026-11-26", "TSE", [{"code": "2344", "name": "華邦電", "foreign": 2_000_000, "trust": -400_000, "dealer": 0, "total": 1_600_000}], "test")
        with database.get_connection() as connection:
            connection.execute("INSERT INTO margin_daily (trade_date, stock_code, market, margin_buy, margin_sell, margin_repay, margin_prev, margin_balance, margin_limit, short_balance, source, updated_at) "
                               "VALUES ('2026-11-26', '2344', 'TSE', 0, 0, 0, 0, 31000, 0, 0, 'test', 'x')")
        got = module.daily_extras("2344")
        self.assertEqual(got["2026-11-25"], {"instNet": 1500, "marginBal": 30000})
        self.assertEqual(got["2026-11-26"], {"instNet": 1600, "marginBal": 31000})


class RunOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._state.update({"running": False, "computedKey": None})

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_run_once_computes_and_caches_even_when_finmind_fails(self) -> None:
        days = weekdays(date(2025, 8, 1), 330)
        with database.get_connection() as connection:
            connection.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES ('1111', ?, 50, 51, 49, 50, 5000)",
                                   [(d + "T00:00:00",) for d in days])

        def boom(params):
            raise RuntimeError("HTTP 400: nope")

        with patch.object(module, "_finmind", boom):
            out = module.run_once()
        self.assertEqual(out["status"], "ok")
        self.assertIsNone(module._state["lastError"])
        self.assertTrue(any("HTTP 400" in e for e in module._state["backfill"]["errors"]))
        self.assertEqual(module.cached()["asOf"], days[-1])
        with patch.object(module, "_finmind", boom):
            self.assertEqual(module.run_once()["status"], "unchanged")

    def test_fetch_day_tries_start_date_only_first(self) -> None:
        seen = []

        def fetch(params):
            seen.append(dict(params))
            if "end_date" in params:
                raise RuntimeError("HTTP 400")
            return {"data": []}

        module._fetch_day(fetch, "TaiwanStockDayTrading", "2026-11-26")
        self.assertEqual(seen, [{"dataset": "TaiwanStockDayTrading", "start_date": "2026-11-26"}])

        def only_range(params):
            if "end_date" not in params:
                raise RuntimeError("HTTP 400: a")
            return {"data": [1]}

        self.assertEqual(module._fetch_day(only_range, "X", "2026-11-26"), {"data": [1]})
