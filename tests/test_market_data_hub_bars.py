from __future__ import annotations

import unittest
from unittest.mock import patch
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
    total_amount: float = 0,
    simtrade: bool = False,
    intraday_odd: bool = False,
):
    dt = datetime(2026, 8, 7, hour, minute, second, tzinfo=TW_TZ)
    return {
        "code": code,
        "close": price,
        "volume": volume,
        "tick_type": tick_type,
        "amount": amount,
        "total_amount": total_amount,
        "tick_time": dt.isoformat(),
        "simtrade": simtrade,
        "intraday_odd": intraday_odd,
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
        # 主力大單預設只看單筆張數（≥20 張）：跟另一台工具的主力累計逐筆對照後確認它沒有金額
        # 門檻；2 張、120 萬那筆不算主力。
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 25, 9, 0, 1, tick_type=1))
        hub.on_stock_tick(tick("2330", 99.5, 30, 9, 0, 2, tick_type=2))
        hub.on_stock_tick(tick("2330", 100.5, 2, 9, 0, 3, tick_type=1, amount=1_200_000))
        hub.on_stock_tick(tick("2330", 100.0, 3, 9, 0, 4, tick_type=0))

        bar = hub.get_live_bars_1m("2330")[0]
        self.assertEqual(bar["buy_volume"], 27)
        self.assertEqual(bar["sell_volume"], 30)
        self.assertEqual(bar["neutral_volume"], 3)
        self.assertEqual(bar["main_buy_volume"], 25)
        self.assertEqual(bar["main_sell_volume"], 30)
        self.assertEqual(bar["main_net_volume"], -5)
        self.assertEqual(bar["main_buy_amount"], 2_500_000)
        self.assertEqual(bar["main_sell_amount"], 2_985_000)
        self.assertEqual(bar["main_net_amount"], -485_000)
        self.assertEqual(bar["main_tick_count"], 2)
        self.assertTrue(bar["main_force_available"])

        five_minute_bar = hub.get_live_bars("2330")[0]
        self.assertEqual(five_minute_bar["main_net_volume"], -5)
        self.assertTrue(five_minute_bar["main_force_available"])

    def test_amount_threshold_counts_small_lots_only_when_enabled(self):
        import market_data_hub as hub_module

        with patch.object(hub_module, "MAIN_FORCE_MIN_AMOUNT", 1_000_000.0):
            hub = MarketDataHub()
            hub.on_stock_tick(tick("2330", 100.0, 25, 9, 0, 1, tick_type=1))
            hub.on_stock_tick(tick("2330", 100.5, 2, 9, 0, 3, tick_type=1, amount=1_200_000))
            bar = hub.get_live_bars_1m("2330")[0]
        self.assertEqual(bar["main_buy_volume"], 27)
        self.assertEqual(bar["main_buy_amount"], 3_700_000)
        self.assertEqual(bar["main_tick_count"], 2)

    def test_total_amount_takes_the_latest_cumulative_value_per_bar(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 1, 9, 0, 1, total_amount=1_000_000))
        hub.on_stock_tick(tick("2330", 100.5, 1, 9, 0, 2, total_amount=1_500_000))
        hub.on_stock_tick(tick("2330", 100.0, 1, 9, 1, 1, total_amount=1_800_000))

        bars_1m = hub.get_live_bars_1m("2330")
        self.assertEqual(bars_1m[0]["total_amount"], 1_500_000)
        self.assertEqual(bars_1m[1]["total_amount"], 1_800_000)

        bars_5m = hub.get_live_bars("2330")
        self.assertEqual(bars_5m[0]["total_amount"], 1_800_000)

    def test_total_amount_ignores_out_of_order_smaller_values(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 1, 9, 0, 1, total_amount=2_000_000))
        hub.on_stock_tick(tick("2330", 100.5, 1, 9, 0, 2, total_amount=500_000))

        bar = hub.get_live_bars_1m("2330")[0]
        self.assertEqual(bar["total_amount"], 2_000_000)

    def test_total_amount_defaults_to_zero_when_absent(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 1, 9, 0, 1))

        bar = hub.get_live_bars_1m("2330")[0]
        self.assertEqual(bar["total_amount"], 0)

    def test_pre_market_simtrade_ticks_do_not_create_bars(self):
        # 08:30-09:00盤前試撮：即使沒收盤時間限制，simtrade旗標本身就該擋掉。
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 105.0, 1, 8, 45, simtrade=True))
        hub.on_stock_tick(tick("2330", 106.0, 1, 8, 59, simtrade=True))
        self.assertEqual(hub.get_live_bars_1m("2330"), [])
        self.assertEqual(hub.get_live_bars("2330"), [])

    def test_pre_market_ticks_without_simtrade_flag_are_still_filtered_by_time(self):
        # 保守起見同時用時間邊界擋，即使simtrade旗標沒設對也不該漏網。
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 105.0, 1, 8, 50))
        self.assertEqual(hub.get_live_bars_1m("2330"), [])

    def test_after_hours_ticks_are_filtered_from_bars(self):
        # 13:30後(例如14:00-14:30盤後定價)不該被聚合成假的盤中5分K。
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 1, 14, 0))
        self.assertEqual(hub.get_live_bars_1m("2330"), [])

    def test_intraday_odd_lot_ticks_are_filtered_from_bars(self):
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 100.0, 1, 10, 0, intraday_odd=True))
        self.assertEqual(hub.get_live_bars_1m("2330"), [])

    def test_pre_market_tick_does_not_contaminate_the_first_real_bar(self):
        # 這是實際回報的bug場景：盤前試撮tick若沒被擋掉，會被誤當成
        # 09:00-09:05這根「905基準」bar，讓intraday_kline_signals後續
        # 所有訊號都建立在錯誤的基準上。確認盤前試撮不會混進第一根
        # 真正的09:00分K。
        hub = MarketDataHub()
        hub.on_stock_tick(tick("2330", 999.0, 100, 8, 50, simtrade=True))
        hub.on_stock_tick(tick("2330", 100.0, 1, 9, 0, 1))
        hub.on_stock_tick(tick("2330", 101.0, 1, 9, 0, 30))

        bars_1m = hub.get_live_bars_1m("2330")
        self.assertEqual(len(bars_1m), 1)
        self.assertEqual(bars_1m[0]["open"], 100.0)  # 不是盤前試撮的999.0
        self.assertEqual(bars_1m[0]["high"], 101.0)


if __name__ == "__main__":
    unittest.main()
