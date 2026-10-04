"""休市日／交易日 08:45 前，全站改顯示上一個交易日收盤（tw-groups worker 用的快照端點）。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import persistent_app
import session_close as module
from database import initialize_database, save_bars

TW = timezone(timedelta(hours=8))
UTC = timezone.utc
FAKE_GROUPS = {"面板": [("A", "甲"), ("B", "乙"), ("C", "丙")], "股期標的": [("A", "甲"), ("D", "丁"), ("E", "戊")]}
SUNDAY = datetime(2026, 10, 4, 12, 0, tzinfo=TW)


def _bars():
    return {
        "A": {"2026-10-02": (40.45, 169), "2026-10-01": (38.3, 200)},   # 一般：+5.61%
        "B": {"2026-10-02": (104.5, 50), "2026-10-01": (95.0, 60)},    # 漲停（95 → 104.5）
        "C": {"2026-10-01": (10.0, 5)},                                # 10/02 的日K沒跟上 → 不列
        "D": {"2026-10-02": (20.0, 7)},                                # 沒有 10/01 → 有收盤但沒 pct
        "E": {"2026-10-02": (85.5, 30), "2026-10-01": (95.0, 40)},     # 跌停（95 → 85.5）
    }


class LimitPriceTests(unittest.TestCase):
    def test_limit_prices_follow_tick_rules(self) -> None:
        self.assertEqual(module.limit_prices(95.0), (104.5, 85.5))
        self.assertEqual(module.limit_prices(38.3), (42.1, 34.5))     # 友達：42.13 → 42.10、34.47 → 34.50
        self.assertEqual(module.limit_prices(251.0), (276.0, 226.0))  # 鴻海：跟 TWSE 的 u／w 一致
        self.assertEqual(module.limit_prices(1000.0), (1100.0, 900.0))
        self.assertEqual(module.limit_prices(9.5), (10.45, 8.55))


class HoldRuleTests(unittest.TestCase):
    def test_weekend_and_holiday_hold_all_day(self) -> None:
        self.assertTrue(module.should_hold_previous_close(SUNDAY))
        self.assertTrue(module.should_hold_previous_close(datetime(2026, 10, 3, 23, 59, tzinfo=TW)))  # 週六
        self.assertTrue(module.should_hold_previous_close(datetime(2026, 10, 9, 10, 0, tzinfo=TW)))   # 國慶日補假

    def test_trading_day_holds_only_before_0845(self) -> None:
        self.assertTrue(module.should_hold_previous_close(datetime(2026, 10, 5, 8, 44, tzinfo=TW)))
        self.assertFalse(module.should_hold_previous_close(datetime(2026, 10, 5, 8, 45, tzinfo=TW)))
        self.assertFalse(module.should_hold_previous_close(datetime(2026, 10, 5, 13, 0, tzinfo=TW)))


class ComputeSnapshotTests(unittest.TestCase):
    def test_sunday_snapshot_uses_last_two_market_dates(self) -> None:
        with patch.object(module, "STOCK_GROUPS", FAKE_GROUPS), patch.object(module, "_load_recent_bars", lambda codes, today: _bars()):
            result = module.compute_session_close(SUNDAY)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["today"], "2026-10-04")
        self.assertFalse(result["tradingDay"])
        self.assertTrue(result["held"])
        self.assertEqual(result["session"], "2026-10-02")
        self.assertEqual(result["prevSession"], "2026-10-01")
        self.assertEqual(sorted(result["stocks"]), ["A", "B", "D", "E"])
        self.assertEqual(result["count"], 4)
        a = result["stocks"]["A"]
        self.assertEqual((a["close"], a["prevClose"], a["change"], a["pct"], a["volume"]), (40.45, 38.3, 2.15, 5.61, 169))
        self.assertFalse(a["limitUp"]); self.assertFalse(a["limitDown"])
        self.assertTrue(result["stocks"]["B"]["limitUp"]); self.assertFalse(result["stocks"]["B"]["limitDown"])
        self.assertTrue(result["stocks"]["E"]["limitDown"]); self.assertFalse(result["stocks"]["E"]["limitUp"])
        d = result["stocks"]["D"]
        self.assertEqual((d["close"], d["prevClose"], d["change"], d["pct"]), (20.0, None, None, None))

    def test_trading_day_afternoon_is_not_held(self) -> None:
        with patch.object(module, "STOCK_GROUPS", FAKE_GROUPS), patch.object(module, "_load_recent_bars", lambda codes, today: _bars()):
            result = module.compute_session_close(datetime(2026, 10, 5, 14, 0, tzinfo=TW))
        self.assertTrue(result["tradingDay"])
        self.assertFalse(result["held"])
        self.assertEqual(result["session"], "2026-10-02")  # 日K還沒有 10/05，session 仍是上一個交易日；worker 不會用

    def test_no_bars_at_all(self) -> None:
        with patch.object(module, "STOCK_GROUPS", FAKE_GROUPS), patch.object(module, "_load_recent_bars", lambda codes, today: {}):
            result = module.compute_session_close(SUNDAY)
        self.assertIsNone(result["session"]); self.assertEqual(result["stocks"], {}); self.assertTrue(result["held"])


class DatabaseBackedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed(self, code: str, year: int, month: int, day: int, close: float, volume: int) -> None:
        save_bars("bars_1d", code, [{
            "time": datetime(year, month, day, tzinfo=UTC),
            "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": volume,
        }])

    def test_loads_only_bars_before_today_within_lookback(self) -> None:
        self._seed("2330", 2026, 10, 1, 100.0, 1000)
        self._seed("2330", 2026, 10, 2, 110.0, 2000)
        self._seed("2330", 2026, 10, 4, 999.0, 1)      # 今天的（不該存在）要排除
        self._seed("2330", 2026, 9, 1, 50.0, 10)       # 超過回看天數
        self._seed("2317", 2026, 10, 2, 251.0, 86)     # 沒有 10/01 → 沒 pct
        with patch.object(module, "STOCK_GROUPS", {"半導體": [("2330", "台積電"), ("2317", "鴻海")]}):
            result = module.compute_session_close(SUNDAY)
        self.assertEqual(result["session"], "2026-10-02")
        self.assertEqual(result["prevSession"], "2026-10-01")
        t = result["stocks"]["2330"]
        self.assertEqual((t["close"], t["prevClose"], t["change"], t["pct"], t["volume"]), (110.0, 100.0, 10.0, 10.0, 2000))
        self.assertTrue(t["limitUp"])
        h = result["stocks"]["2317"]
        self.assertEqual((h["close"], h["pct"], h["volume"]), (251.0, None, 86))


class EndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        module._cache.update({"key": None, "at": 0.0, "value": None})

    def test_endpoint_returns_snapshot_and_caches(self) -> None:
        client = TestClient(persistent_app.app)
        with patch.object(module, "STOCK_GROUPS", FAKE_GROUPS), patch.object(module, "_now", lambda: SUNDAY), \
             patch.object(module, "_load_recent_bars", lambda codes, today: _bars()):
            response = client.get("/api/hub/session-close")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["held"])
        self.assertEqual(payload["session"], "2026-10-02")
        self.assertEqual(payload["stocks"]["A"]["pct"], 5.61)
        # 5 分鐘內同一天同一個 held 狀態直接用快取，不再查日K
        with patch.object(module, "_now", lambda: SUNDAY), patch.object(module, "_load_recent_bars", lambda codes, today: {}):
            cached = client.get("/api/hub/session-close").json()
        self.assertEqual(cached["stocks"]["A"]["pct"], 5.61)
        # held 翻面（交易日 08:45）就是新的快取鍵，會重算
        monday = datetime(2026, 10, 5, 8, 45, tzinfo=TW)
        with patch.object(module, "_now", lambda: monday), patch.object(module, "_load_recent_bars", lambda codes, today: {}):
            fresh = client.get("/api/hub/session-close").json()
        self.assertFalse(fresh["held"]); self.assertEqual(fresh["stocks"], {})


if __name__ == "__main__":
    unittest.main()
