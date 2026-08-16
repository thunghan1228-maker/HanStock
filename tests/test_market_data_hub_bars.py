from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from market_data_hub import MarketDataHub


TW_TZ = timezone(timedelta(hours=8))


def tick(
    code: str,
    price: float,
    volume: int,
    hour: int,
    minute: int,
    second: int = 0,
    *,
    tick_type: int = 0,
    amount: float = 0,
):
    dt = datetime(2026, 8, 7, hour, minute, second, tzinfo=TW_TZ)
    return {
        "code": code,
        "close": price,
        "volume": volume,
        "tick_type": tick_type,
        "amount": amount,
        "tick_time": dt.isoformat(),
    }


class MarketDataHubBarTests(unittest.TestCase):
    def test_one_minute_ohlcv_and_rollover(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 2, 9, 0, 5))
        hub.on_stock_tick(tick("2330", 102.0, 3, 9, 0, 40))
        hub.on_stock_tick(tick("2330", 99.0, 4, 9, 1, 1))

        bars = hub.get_live_bars_1m("2330")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["open"], 100.0)
        self.assertEqual(bars[0]["high"], 102.0)
        self.assertEqual(bars[0]["low"], 100.0)
        self.assertEqual(bars[0]["close"], 102.0)
        self.assertEqual(bars[0]["volume"], 5)
        self.assertEqual(bars[0]["tick_count"], 2)
        self.assertEqual(bars[1]["open"], 99.0)
        self.assertEqual(bars[1]["volume"], 4)

    def test_five_minute_aggregator_remains_compatible(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2344", 10.0, 1, 9, 0, 1))
        hub.on_stock_tick(tick("2344", 12.0, 2, 9, 1, 1))
        hub.on_stock_tick(tick("2344", 9.0, 3, 9, 4, 59))
        hub.on_stock_tick(tick("2344", 11.0, 4, 9, 5, 0))

        bars_1m = hub.get_live_bars_1m("2344")
        bars_5m = hub.get_live_bars("2344")
        self.assertEqual(len(bars_1m), 4)
        self.assertEqual(len(bars_5m), 2)
        self.assertEqual(bars_5m[0]["open"], 10.0)
        self.assertEqual(bars_5m[0]["high"], 12.0)
        self.assertEqual(bars_5m[0]["low"], 9.0)
        self.assertEqual(bars_5m[0]["close"], 9.0)
        self.assertEqual(bars_5m[0]["volume"], 6)

    def test_delayed_tick_does_not_move_bar_time_backwards(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 101.0, 1, 9, 1, 1))
        hub.on_stock_tick(tick("2330", 99.0, 1, 9, 0, 59))
        bars = hub.get_live_bars_1m("2330")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["open"], 101.0)
        self.assertEqual(bars[0]["close"], 101.0)

    def test_batch_returns_independent_symbols(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 1, 9, 0, 1))
        hub.on_stock_tick(tick("2344", 20.0, 2, 9, 0, 2))

        result = hub.get_live_bars_1m_batch(["2330", "2344", "2408"])
        self.assertEqual(len(result["2330"]), 1)
        self.assertEqual(len(result["2344"]), 1)
        self.assertEqual(result["2408"], [])

    def test_hub_status_includes_one_minute_stats(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 1, 9, 0, 1))
        hub.on_stock_tick(tick("2330", 101.0, 1, 9, 1, 1))
        status = hub.get_hub_status()
        self.assertEqual(status["total_bars_1m_completed"], 1)
        self.assertEqual(status["bar_aggregator_1m_codes"], 1)

    def test_main_force_buy_sell_volume_is_aggregated_per_bar(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 25, 9, 0, 1, tick_type=1))
        hub.on_stock_tick(tick("2330", 99.5, 30, 9, 0, 2, tick_type=2))
        hub.on_stock_tick(tick("2330", 100.5, 2, 9, 0, 3, tick_type=1, amount=1_200_000))
        hub.on_stock_tick(tick("2330", 100.0, 3, 9, 0, 4, tick_type=0))

        bar = hub.get_live_bars_1m("2330")[0]
        self.assertEqual(bar["buy_volume"], 27)
        self.assertEqual(bar["sell_volume"], 30)
        self.assertEqual(bar["neutral_volume"], 3)
        self.assertEqual(bar["main_buy_volume"], 27)
        self.assertEqual(bar["main_sell_volume"], 30)
        self.assertEqual(bar["main_net_volume"], -3)
        self.assertEqual(bar["main_buy_amount"], 3_700_000)
        self.assertEqual(bar["main_sell_amount"], 2_985_000)
        self.assertEqual(bar["main_net_amount"], 715_000)
        self.assertEqual(bar["main_tick_count"], 3)
        self.assertTrue(bar["main_force_available"])

        five_minute_bar = hub.get_live_bars("2330")[0]
        self.assertEqual(five_minute_bar["main_net_volume"], -3)
        self.assertTrue(five_minute_bar["main_force_available"])


if __name__ == "__main__":
    unittest.main()
