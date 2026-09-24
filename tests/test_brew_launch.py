"""醞釀／發動選股（1＝醞釀整理形態、2＝發動突破）的日K部分。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch as module
import persistent_app


def _dates(n: int, end: str = "2026-09-23") -> list[str]:
    """n 個平日（舊到新），最後一天是 end。"""
    out: list[str] = []
    day = date.fromisoformat(end)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return list(reversed(out))


def _bars(closes: list[float], *, spread: float = 0.01, volume: int = 1000, end: str = "2026-09-23"):
    days = _dates(len(closes), end)
    return [(d, c * (1 + spread), c * (1 - spread), c, volume) for d, c in zip(days, closes)]


def _uptrend_then_box(n: int = 250, box_days: int = 12, top: float = 100.0) -> list[float]:
    """一路緩漲到 top，最後 box_days 天在 top 附近 ±1% 橫盤整理（均線多頭排列、短均線糾結）。"""
    rise = [50 + (top - 50) * i / (n - box_days - 1) for i in range(n - box_days)]
    box = [top * (1 + (0.01 if i % 2 else -0.01)) for i in range(box_days)]
    return rise + box


class AnalyzeStockTests(unittest.TestCase):
    def test_consolidation_after_uptrend_is_brewing(self) -> None:
        info = module.analyze_stock(_bars(_uptrend_then_box()))
        self.assertIsNotNone(info)
        self.assertTrue(info["brewing"])
        self.assertGreaterEqual(info["score"], module.BREW_MIN_SCORE)
        self.assertLessEqual(info["boxRangePct"], module.BOX_RANGE_MAX_PCT)
        self.assertLessEqual(info["maSpreadPct"], module.MA_SPREAD_MAX_PCT)
        self.assertEqual(info["asOf"], "2026-09-23")

    def test_box_is_last_ten_days_high_low(self) -> None:
        closes = _uptrend_then_box()
        bars = _bars(closes)
        info = module.analyze_stock(bars)
        self.assertAlmostEqual(info["boxHigh"], max(b[1] for b in bars[-10:]))
        self.assertAlmostEqual(info["boxLow"], min(b[2] for b in bars[-10:]))
        self.assertAlmostEqual(info["prevClose"], closes[-1])

    def test_ma_sums_let_frontend_recompute_live_ma(self) -> None:
        closes = _uptrend_then_box()
        info = module.analyze_stock(_bars(closes))
        live = 123.45
        for period in module.MA_PERIODS:
            expected = (sum(closes[-(period - 1):]) + live) / period
            self.assertAlmostEqual((info["maSums"][str(period)] + live) / period, expected, places=3)
            self.assertAlmostEqual(info["ma"][str(period)], sum(closes[-period:]) / period, places=3)

    def test_score_matches_existing_fifteen_pair_algorithm(self) -> None:
        # 均線完全多頭排列：15 組全對 = 15 分；完全空頭排列 = 0 分
        self.assertEqual(module.ma_alignment_score({5: 6, 10: 5, 20: 4, 60: 3, 120: 2, 240: 1}), 15)
        self.assertEqual(module.ma_alignment_score({5: 1, 10: 2, 20: 3, 60: 4, 120: 5, 240: 6}), 0)
        self.assertEqual(module.ma_alignment_score({5: 3, 10: 4, 20: 5, 60: 2, 120: 1.5, 240: 1}), 12)

    def test_wide_box_is_not_brewing(self) -> None:
        closes = _uptrend_then_box()
        closes[-5] = closes[-5] * 1.25  # 箱子裡一根爆衝，箱頂到箱底超過 15%
        info = module.analyze_stock(_bars(closes))
        self.assertGreater(info["boxRangePct"], module.BOX_RANGE_MAX_PCT)
        self.assertFalse(info["brewing"])

    def test_close_below_monthly_line_is_not_brewing(self) -> None:
        closes = _uptrend_then_box()
        closes[-1] = closes[-1] * 0.9  # 最後一天收在月線下面
        info = module.analyze_stock(_bars(closes))
        self.assertLess(info["prevClose"], info["ma"]["20"])
        self.assertFalse(info["brewing"])

    def test_short_mas_not_tangled_is_not_brewing(self) -> None:
        # 一路急漲、沒有整理：5 日線遠高於 20 日線，三條差距超過 4%
        closes = [50 * (1.01 ** i) for i in range(250)]
        info = module.analyze_stock(_bars(closes, spread=0.005))
        self.assertGreater(info["maSpreadPct"], module.MA_SPREAD_MAX_PCT)
        self.assertFalse(info["brewing"])
        self.assertEqual(info["score"], 15)

    def test_less_than_240_bars_returns_none(self) -> None:
        self.assertIsNone(module.analyze_stock(_bars(_uptrend_then_box(n=239))))

    def test_average_volume_is_last_five_days(self) -> None:
        bars = _bars(_uptrend_then_box())
        bars = bars[:-5] + [(d, h, l, c, v) for (d, h, l, c, _v), v in zip(bars[-5:], (100, 200, 300, 400, 500))]
        self.assertEqual(module.analyze_stock(bars)["avgVol5"], 300.0)


FAKE_GROUPS = {
    "半導體": [("2330", "台積電"), ("2303", "聯電")],
    "被動元件": [("2327", "國巨"), ("6173", "信昌電")],
    "股期標的": [("2330", "台積電"), ("2454", "聯發科")],
}


class ComputeTests(unittest.TestCase):
    def setUp(self) -> None:
        module._cache.update({"key": None, "at": 0.0, "value": None})

    def _run(self, bars_by_code, market_values=None, session="2026-09-24"):
        with patch.object(module, "STOCK_GROUPS", FAKE_GROUPS), \
                patch.object(module, "_load_bars", lambda codes, session: {c: b for c, b in bars_by_code.items() if c in codes}), \
                patch.object(module, "_load_market_values", lambda codes, session: market_values or {}):
            return module.compute_brew_launch(session=session)

    def test_universe_excludes_stock_futures_only_codes(self) -> None:
        with patch.object(module, "STOCK_GROUPS", FAKE_GROUPS):
            self.assertEqual(module.group_codes(), ["2303", "2327", "2330", "6173"])

    def test_stale_insufficient_and_shares(self) -> None:
        good = _bars(_uptrend_then_box())
        old = _bars(_uptrend_then_box(), end="2026-09-22")          # 最後一根比全市場舊：停牌／沒跟上
        short = _bars(_uptrend_then_box(n=200))                       # 不到 240 根
        result = self._run(
            {"2330": good, "2327": old, "6173": short},
            market_values={"2330": ("2026-09-23", good[-1][3] * 1000 * 26_000_000)},
        )
        self.assertEqual(result["asOf"], "2026-09-23")
        self.assertEqual(result["session"], "2026-09-24")
        self.assertEqual(sorted(result["stocks"]), ["2330"])
        self.assertEqual(result["stale"], ["2327"])
        self.assertEqual(sorted(result["insufficient"]), ["2303", "6173"])  # 2303 完全沒日K
        self.assertAlmostEqual(result["stocks"]["2330"]["sharesLots"], 26_000_000.0)
        self.assertEqual(result["brewingCount"], 1)
        self.assertEqual(result["rules"]["launchMinScore"], 11)

    def test_missing_market_value_leaves_shares_none(self) -> None:
        result = self._run({"2330": _bars(_uptrend_then_box())})
        self.assertIsNone(result["stocks"]["2330"]["sharesLots"])


class DatabaseIntegrationTests(unittest.TestCase):
    """實際走 SQLite：bars_1d 跟 stock_fundamentals_daily 的查詢（日期字串比較、IN 批次）。"""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        import database

        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_reads_bars_before_session_and_latest_market_value(self) -> None:
        from datetime import timezone

        from database import save_bars
        from disposition_fundamentals_store import save_fundamentals_rows

        closes = _uptrend_then_box()
        bars = _bars(closes)
        rows = [{"time": datetime.fromisoformat(d).replace(tzinfo=timezone.utc), "open": c, "high": h, "low": l, "close": c, "volume": v}
                for d, h, l, c, v in bars]
        # session 當天（09/24）已經有一根（收盤後日K進來了）：箱子不能把它算進去
        rows.append({"time": datetime(2026, 9, 24, tzinfo=timezone.utc), "open": 150, "high": 160, "low": 140, "close": 150, "volume": 9})
        save_bars("bars_1d", "2330", rows)
        save_fundamentals_rows([
            {"code": "2330", "tradeDate": "2026-09-22", "marketValue": closes[-2] * 1000 * 10_000},
            {"code": "2330", "tradeDate": "2026-09-23", "marketValue": closes[-1] * 1000 * 26_000},
        ])
        with patch.object(module, "STOCK_GROUPS", FAKE_GROUPS):
            result = module.compute_brew_launch(session="2026-09-24")
        info = result["stocks"]["2330"]
        self.assertEqual(result["asOf"], "2026-09-23")
        self.assertAlmostEqual(info["prevClose"], closes[-1])
        self.assertAlmostEqual(info["boxHigh"], max(b[1] for b in bars[-10:]))
        self.assertAlmostEqual(info["sharesLots"], 26_000.0)  # 取最近一天的市值 ÷ 那天收盤
        self.assertTrue(info["brewing"])


class SessionDateTests(unittest.TestCase):
    def test_weekday_is_today(self) -> None:
        now = datetime(2026, 9, 24, 10, 0, tzinfo=module.TW_TZ)  # 週四
        self.assertEqual(module.session_date(now), "2026-09-24")

    def test_weekend_uses_last_trading_day(self) -> None:
        now = datetime(2026, 9, 26, 10, 0, tzinfo=module.TW_TZ)  # 週六
        with patch.object(module, "_latest_bar_date", lambda: "2026-09-25"):
            self.assertEqual(module.session_date(now), "2026-09-25")


class EndpointTests(unittest.TestCase):
    def test_endpoint_returns_payload(self) -> None:
        fake = {"status": "ok", "stocks": {}, "rules": module.RULES}
        with patch.object(module, "get_brew_launch", lambda: fake):
            response = TestClient(persistent_app.app).get("/api/hub/brew-launch")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_endpoint_codes_filter(self) -> None:
        fake = {"status": "ok", "stockCount": 3, "stocks": {"2330": {"score": 15}, "2303": {"score": 9}, "6173": {"score": 12}}}
        with patch.object(module, "get_brew_launch", lambda: fake):
            body = TestClient(persistent_app.app).get("/api/hub/brew-launch?codes=2330, 6173").json()
        self.assertEqual(sorted(body["stocks"]), ["2330", "6173"])
        self.assertEqual(body["stockCount"], 3)  # 統計照舊，只過濾 stocks
        self.assertEqual(len(fake["stocks"]), 3)  # 不能改到快取裡的原始資料


if __name__ == "__main__":
    unittest.main()
