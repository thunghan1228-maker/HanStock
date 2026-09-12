import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import database
from main_force_store import save_main_force_bars
from four_gate_signals import (
    _minute_net_ratios,
    _price_position,
    evaluate_ticker,
)
from otc_index import TW_TZ


class FakeHub:
    def get_live_bars_1m(self, code):
        return []


class FourGatePriceComputationTests(unittest.TestCase):
    def test_minute_net_ratios_tracks_current_and_previous_minute(self):
        base_ts = int(datetime(2026, 9, 14, 9, 0, tzinfo=TW_TZ).timestamp() * 1000)
        bars = [
            {"ts": base_ts, "main_buy_amount": 1000, "main_sell_amount": 0},
            {"ts": base_ts + 60_000, "main_buy_amount": 1000, "main_sell_amount": 0},
            {"ts": base_ts + 120_000, "main_buy_amount": 0, "main_sell_amount": 500},
        ]
        current_ratio, previous_ratio = _minute_net_ratios(bars, "2026-09-14", 9 * 60 + 2)
        self.assertEqual(current_ratio, -1.0)  # 第三分鐘全部是賣出
        self.assertEqual(previous_ratio, 1.0)  # 第二分鐘全部是買進

    def test_price_position_computes_vwap_and_first_bar_range(self):
        base_ts = int(datetime(2026, 9, 14, 9, 0, tzinfo=TW_TZ).timestamp() * 1000)
        bars = [
            {"ts": base_ts, "open": 95, "high": 100, "low": 95, "close": 98, "volume": 1000},
            {"ts": base_ts + 30 * 60_000, "open": 98, "high": 112, "low": 97, "close": 110, "volume": 1000},
        ]
        position = _price_position(bars, "2026-09-14")
        self.assertAlmostEqual(position["vwap"], 102.0, places=1)
        self.assertEqual(position["firstHigh"], 100)
        self.assertEqual(position["firstLow"], 95)
        self.assertEqual(position["price"], 110)


class FourGateEvaluateTickerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_main_force_bars(self, ticker, now, *, buy=4_000_000, sell=0):
        base = now.replace(hour=9, minute=0, second=0, microsecond=0)
        bars = []
        minute = 0
        while base + timedelta(minutes=minute) <= now:
            ts = int((base + timedelta(minutes=minute)).timestamp() * 1000)
            bars.append({
                "ts": ts, "main_buy_volume": 40, "main_sell_volume": 0,
                "main_buy_amount": buy, "main_sell_amount": sell,
                "main_force_available": True,
            })
            minute += 1
        save_main_force_bars(ticker, "1m", bars)

    def test_bullish_force_and_price_position_returns_buy_signal(self):
        now = datetime(2026, 9, 14, 9, 35, tzinfo=TW_TZ)  # 週一
        self._seed_main_force_bars("2330", now)  # 全部是買超 -> 淨額比 100%
        bars_5m = [
            {"ts": int(now.replace(hour=9, minute=0).timestamp() * 1000), "open": 95, "high": 100, "low": 95, "close": 98, "volume": 1000},
            {"ts": int(now.timestamp() * 1000), "open": 98, "high": 112, "low": 97, "close": 110, "volume": 1000},
        ]
        with patch("four_gate_signals.get_resilient_stock_bars", return_value={"bars": bars_5m}):
            signal = evaluate_ticker(service=object(), hub=FakeHub(), ticker="2330", now=now)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["kind"], "fourGateBuy")
        self.assertEqual(signal["ticker"], "2330")

    def test_weak_force_ratio_blocks_signal(self):
        # 買超但沒有到50%淨額比門檻（假設一半買一半賣的比例更弱）
        now = datetime(2026, 9, 14, 9, 35, tzinfo=TW_TZ)
        self._seed_main_force_bars("2330", now, buy=1_100_000, sell=1_000_000)  # 淨額比僅約4.8%
        bars_5m = [
            {"ts": int(now.replace(hour=9, minute=0).timestamp() * 1000), "open": 95, "high": 100, "low": 95, "close": 98, "volume": 1000},
            {"ts": int(now.timestamp() * 1000), "open": 98, "high": 112, "low": 97, "close": 110, "volume": 1000},
        ]
        with patch("four_gate_signals.get_resilient_stock_bars", return_value={"bars": bars_5m}):
            signal = evaluate_ticker(service=object(), hub=FakeHub(), ticker="2330", now=now)
        self.assertIsNone(signal)

    def test_price_not_breaking_first_bar_high_blocks_signal(self):
        now = datetime(2026, 9, 14, 9, 35, tzinfo=TW_TZ)
        self._seed_main_force_bars("2330", now)  # 主力淨額比條件通過
        bars_5m = [
            {"ts": int(now.replace(hour=9, minute=0).timestamp() * 1000), "open": 95, "high": 120, "low": 95, "close": 98, "volume": 1000},
            {"ts": int(now.timestamp() * 1000), "open": 98, "high": 112, "low": 97, "close": 110, "volume": 1000},
        ]
        with patch("four_gate_signals.get_resilient_stock_bars", return_value={"bars": bars_5m}):
            signal = evaluate_ticker(service=object(), hub=FakeHub(), ticker="2330", now=now)
        self.assertIsNone(signal)  # 現價110沒有突破首5分高點120

    def test_outside_trading_window_blocks_signal(self):
        now = datetime(2026, 9, 14, 8, 0, tzinfo=TW_TZ)
        signal = evaluate_ticker(service=object(), hub=FakeHub(), ticker="2330", now=now)
        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main()
