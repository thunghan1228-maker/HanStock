"""台股休市日曆：中秋節（9/25）、教師節（9/28）休市，9/29 才是下一個交易日；大戶力沿用規則在假日整天都留。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

import persistent_app
import trading_days as module

TW = timezone(timedelta(hours=8))


class TradingDayTests(unittest.TestCase):
    def test_holidays_and_weekends(self) -> None:
        self.assertTrue(module.is_trading_day("2026-09-24"))                       # 週四
        self.assertFalse(module.is_trading_day("2026-09-25"))                      # 中秋節
        self.assertFalse(module.is_trading_day("2026-09-26"))                      # 週六
        self.assertFalse(module.is_trading_day("2026-09-27"))                      # 週日
        self.assertFalse(module.is_trading_day("2026-09-28"))                      # 教師節
        self.assertTrue(module.is_trading_day("2026-09-29"))                       # 週二
        self.assertFalse(module.is_trading_day(datetime(2026, 9, 25, 10, 0, tzinfo=TW)))
        self.assertTrue(module.is_trading_day(date(2026, 9, 29)))
        self.assertFalse(module.is_trading_day("2026-02-12"))                      # 春節前無交易日

    def test_previous_and_next_trading_day(self) -> None:
        self.assertEqual(module.previous_trading_day("2026-09-29"), date(2026, 9, 24))
        self.assertEqual(module.next_trading_day("2026-09-24"), date(2026, 9, 29))
        self.assertEqual(module.next_trading_day(datetime(2026, 9, 25, 0, 30, tzinfo=TW)), date(2026, 9, 29))

    def test_ranking_hold_rule_keeps_previous_day_through_holidays(self) -> None:
        hold = persistent_app._should_hold_previous_ranking
        self.assertTrue(hold(datetime(2026, 9, 25, 10, 0, tzinfo=TW)))    # 中秋節整天沿用
        self.assertTrue(hold(datetime(2026, 9, 28, 12, 0, tzinfo=TW)))    # 教師節整天沿用
        self.assertTrue(hold(datetime(2026, 9, 29, 8, 30, tzinfo=TW)))    # 交易日 08:45 前沿用
        self.assertFalse(hold(datetime(2026, 9, 29, 8, 45, tzinfo=TW)))   # 08:45 起等開盤


if __name__ == "__main__":
    unittest.main()
