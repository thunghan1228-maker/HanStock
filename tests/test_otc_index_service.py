from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
from otc_index import taipei_trade_date
from otc_index_hub import OtcIndexHub
from otc_index_service import BOOTSTRAP_CALENDAR_DAYS, OtcIndexService
from otc_index_store import load_index_bars_5m, save_index_bars_5m

TW = timezone(timedelta(hours=8))


def shioaji_wall_ns(hour: int, minute: int, *, day: date) -> int:
    """建立 Shioaji KBars.ts 類型的「無時區本地牆鐘」ns 值（收棒標記）。"""
    wall = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
    return int(wall.timestamp() * 1_000_000_000)


def stored_bar(day: date, hour: int, minute: int, close: float = 100.0) -> dict:
    moment = datetime(day.year, day.month, day.day, hour, 0, tzinfo=TW) + timedelta(minutes=minute)
    ts = int(moment.timestamp() * 1000)
    return {"ts": ts, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1}


class FakeKbars(dict):
    """模擬 Shioaji kbars() 回傳物件：各欄位是 list，用 dict.get() 存取。"""


class _TempDatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()


class BootstrapTodayCalendarRangeTests(_TempDatabaseTestCase):
    def test_queries_multiple_calendar_days_not_just_today(self) -> None:
        # 這是使用者回報的核心bug：舊版硬寫死start=end=today，MA20永遠要
        # 等今天自己累積20根5分K(約開盤後1小時40分)才會ready，即使Shioaji
        # kbars()本來就查得到歷史。改成跟stock_history_service.py一樣查
        # 一段日曆天範圍。
        captured = {}

        class FakeApi:
            def kbars(self, contract, start, end):
                captured["start"] = start
                captured["end"] = end
                return FakeKbars(ts=[], Open=[], High=[], Low=[], Close=[], Volume=[])

        service = OtcIndexService()
        fresh_hub = OtcIndexHub()
        with patch("otc_index_service.get_otc_index_hub", return_value=fresh_hub):
            service.bootstrap_today(FakeApi(), contract=object())

        today = datetime.now(TW).date()
        expected_start = (today - timedelta(days=BOOTSTRAP_CALENDAR_DAYS - 1)).isoformat()
        self.assertEqual(captured["start"], expected_start)
        self.assertEqual(captured["end"], today.isoformat())
        self.assertGreater(BOOTSTRAP_CALENDAR_DAYS, 1)

    def test_seeds_hub_with_bars_from_a_previous_trading_day(self) -> None:
        today = datetime.now(TW).date()
        yesterday = today - timedelta(days=1)

        class FakeApi:
            def kbars(self, contract, start, end):
                return FakeKbars(
                    ts=[shioaji_wall_ns(9, 5, day=yesterday), shioaji_wall_ns(9, 5, day=today)],
                    Open=[100.0, 200.0],
                    High=[101.0, 201.0],
                    Low=[99.0, 199.0],
                    Close=[100.5, 200.5],
                    Volume=[0, 0],
                )

        service = OtcIndexService()
        fresh_hub = OtcIndexHub()
        with patch("otc_index_service.get_otc_index_hub", return_value=fresh_hub):
            result = service.bootstrap_today(FakeApi(), contract=object())

        self.assertTrue(result["ok"])
        bars = fresh_hub.get_bars_5m(include_current=True)
        dates = {taipei_trade_date(int(b["ts"])) for b in bars}
        self.assertIn(yesterday.isoformat(), dates)
        self.assertIn(today.isoformat(), dates)


class BootstrapResilienceTests(_TempDatabaseTestCase):
    """使用者實際回報：昨天明明顯示過，重新部署之後又變回「資料蒐集中」。"""

    def test_falls_back_to_stored_bars_when_kbars_fails_and_keeps_the_error_visible(self) -> None:
        today = datetime.now(TW).date()
        yesterday = today - timedelta(days=1)
        save_index_bars_5m([stored_bar(yesterday, 9, 5 * i, 100.0 + i) for i in range(21)])

        class QuotaExhaustedApi:
            def kbars(self, contract, start, end):
                raise RuntimeError("history quota exhausted")

        service = OtcIndexService()
        fresh_hub = OtcIndexHub()
        with patch("otc_index_service.get_otc_index_hub", return_value=fresh_hub):
            result = service.bootstrap_today(QuotaExhaustedApi(), contract=object())

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stored_bars_5m"], 21)
        self.assertEqual(len(fresh_hub.get_bars_5m(include_current=True)), 21)
        status = fresh_hub.get_status()
        self.assertTrue(status["bootstrap_ok"])
        self.assertIn("quota exhausted", status["bootstrap_error"])
        # 舊版把補齊失敗寫在 _error，隨後訂閱成功的 set_subscribed(True) 就把它清掉，
        # 健康檢查看不出任何問題；補齊錯誤要獨立保留。
        fresh_hub.set_subscribed(True)
        self.assertIn("quota exhausted", fresh_hub.get_status()["bootstrap_error"])

    def test_kbars_bars_are_persisted_so_later_restarts_do_not_depend_on_kbars(self) -> None:
        today = datetime.now(TW).date()
        yesterday = today - timedelta(days=1)

        class FakeApi:
            def kbars(self, contract, start, end):
                return FakeKbars(
                    ts=[shioaji_wall_ns(9, 5, day=yesterday), shioaji_wall_ns(9, 10, day=yesterday)],
                    Open=[100.0, 101.0], High=[101.0, 102.0], Low=[99.0, 100.0], Close=[100.5, 101.5], Volume=[0, 0],
                )

        service = OtcIndexService()
        with patch("otc_index_service.get_otc_index_hub", return_value=OtcIndexHub()):
            service.bootstrap_today(FakeApi(), contract=object())

        start = (today - timedelta(days=BOOTSTRAP_CALENDAR_DAYS - 1)).isoformat()
        stored = load_index_bars_5m(start, today.isoformat())
        # 收棒 09:05/09:10 的 1 分 K 起點是 09:04/09:09，分別落在 09:00 與 09:05 兩根 5 分 K。
        self.assertEqual([b["close"] for b in stored], [100.5, 101.5])

    def test_ensure_bootstrapped_retries_after_failure_only_past_the_cooldown(self) -> None:
        calls = []

        class FlakyApi:
            def __init__(self):
                self.fail = True

            def kbars(self, contract, start, end):
                calls.append(1)
                if self.fail:
                    raise RuntimeError("quota")
                today = datetime.now(TW).date()
                return FakeKbars(
                    ts=[shioaji_wall_ns(9, 5, day=today - timedelta(days=1))],
                    Open=[100.0], High=[101.0], Low=[99.0], Close=[100.5], Volume=[0],
                )

        api = FlakyApi()
        service = OtcIndexService()
        service.contract = object()
        fresh_hub = OtcIndexHub()
        with patch("otc_index_service.get_otc_index_hub", return_value=fresh_hub):
            self.assertTrue(service.ensure_bootstrapped(api, run_in_background=False))
            self.assertEqual(len(calls), 1)
            self.assertFalse(fresh_hub.get_status()["bootstrap_ok"])
            # 冷卻時間內不重打 kbars。
            self.assertFalse(service.ensure_bootstrapped(api, run_in_background=False))
            self.assertEqual(len(calls), 1)

            service._last_bootstrap_attempt = float("-inf")
            api.fail = False
            self.assertTrue(service.ensure_bootstrapped(api, run_in_background=False))
            self.assertEqual(len(calls), 2)
            self.assertTrue(fresh_hub.get_status()["bootstrap_ok"])
            # 已經補齊就不再重試。
            service._last_bootstrap_attempt = float("-inf")
            self.assertFalse(service.ensure_bootstrapped(api, run_in_background=False))
            self.assertEqual(len(calls), 2)

    def test_day_rollover_clears_bootstrap_so_ensure_bootstrapped_reseeds(self) -> None:
        now = datetime.now(TW)
        fresh_hub = OtcIndexHub()
        fresh_hub.seed_today([], [stored_bar(now.date(), 9, 0)], now.strftime("%Y-%m-%d"), ok=True)
        self.assertTrue(fresh_hub.get_status()["bootstrap_ok"])

        # 跨日：hub 會把前一天的 K 棒跟 bootstrap 狀態一起清掉，ensure_bootstrapped 才會重補。
        fresh_hub._rollover_if_needed("2099-01-01")
        status = fresh_hub.get_status()
        self.assertFalse(status["bootstrap_ok"])
        self.assertIsNone(status["bootstrap_error"])
        self.assertEqual(status["bar_count_5m"], 0)


if __name__ == "__main__":
    unittest.main()
