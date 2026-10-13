from __future__ import annotations

import types
import unittest
from datetime import datetime, timedelta, timezone

import stock_universe_subscriber as subscriber
from stock_groups import industry_group_codes

TW = timezone(timedelta(hours=8))


class StockUniverseSubscriberTests(unittest.TestCase):
    def test_window_is_trading_day_morning_session_only(self):
        self.assertTrue(subscriber.in_subscribe_window(datetime(2026, 10, 13, 9, 0, tzinfo=TW)))       # 週二盤中
        self.assertTrue(subscriber.in_subscribe_window(datetime(2026, 10, 13, 8, 35, tzinfo=TW)))      # 盤前試撮
        self.assertFalse(subscriber.in_subscribe_window(datetime(2026, 10, 13, 8, 20, tzinfo=TW)))
        self.assertFalse(subscriber.in_subscribe_window(datetime(2026, 10, 13, 14, 0, tzinfo=TW)))     # 收盤後
        self.assertFalse(subscriber.in_subscribe_window(datetime(2026, 10, 17, 9, 0, tzinfo=TW)))      # 週六
        self.assertFalse(subscriber.in_subscribe_window(datetime(2026, 10, 9, 9, 0, tzinfo=TW)))       # 國慶補假（休市）

    def test_subscribe_once_sends_every_industry_group_code(self):
        calls = []

        def ensure(codes):
            calls.append(list(codes))
            return {"newly_subscribed": codes[:3], "already_subscribed": codes[3:], "failed": {}, "main_active_count": 190, "active_count": len(codes)}

        service = types.SimpleNamespace(state=types.SimpleNamespace(logged_in=True), ensure_stock_subscriptions=ensure)
        summary = subscriber.subscribe_once(service)
        expected = sorted(industry_group_codes())
        self.assertEqual(calls, [expected])
        self.assertEqual(summary["requested"], len(expected))
        self.assertEqual(summary["newlySubscribed"], 3)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["active"], len(expected))

    def test_subscribe_once_waits_for_login(self):
        service = types.SimpleNamespace(state=types.SimpleNamespace(logged_in=False), ensure_stock_subscriptions=lambda codes: self.fail("不該訂閱"))
        self.assertEqual(subscriber.subscribe_once(service), {"skipped": "shioaji_not_ready"})


if __name__ == "__main__":
    unittest.main()
