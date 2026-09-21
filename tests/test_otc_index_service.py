from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from otc_index import taipei_trade_date
from otc_index_hub import OtcIndexHub
from otc_index_service import BOOTSTRAP_CALENDAR_DAYS, OtcIndexService

TW = timezone(timedelta(hours=8))


def shioaji_wall_ns(hour: int, minute: int, *, day: date) -> int:
    """建立 Shioaji KBars.ts 類型的「無時區本地牆鐘」ns 值（收棒標記）。"""
    wall = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
    return int(wall.timestamp() * 1_000_000_000)


class FakeKbars(dict):
    """模擬 Shioaji kbars() 回傳物件：各欄位是 list，用 dict.get() 存取。"""


class BootstrapTodayCalendarRangeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
