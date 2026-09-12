import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import database
from main_force_store import save_main_force_bars
from four_gate_signals import (
    _cumulative_amounts,
    _price_position,
    _threshold_rate,
    evaluate_ticker,
)
from otc_index import TW_TZ


class FakeHub:
    def get_live_bars_1m(self, code):
        return []


class FourGateThresholdTests(unittest.TestCase):
    def test_threshold_rate_tiers(self):
        self.assertEqual(_threshold_rate(9 * 60), 0.50)
        self.assertEqual(_threshold_rate(9 * 60 + 29), 0.50)
        self.assertEqual(_threshold_rate(9 * 60 + 30), 0.70)
        self.assertEqual(_threshold_rate(9 * 60 + 59), 0.70)
        self.assertEqual(_threshold_rate(10 * 60), 0.90)
        self.assertEqual(_threshold_rate(10 * 60 + 59), 0.90)
        self.assertEqual(_threshold_rate(11 * 60), 1.20)
        self.assertEqual(_threshold_rate(13 * 60 + 30), 1.20)


class FourGatePriceComputationTests(unittest.TestCase):
    def test_cumulative_amounts_tracks_buy_sell_and_minute_ratios(self):
        base_ts = int(datetime(2026, 9, 14, 9, 0, tzinfo=TW_TZ).timestamp() * 1000)
        bars = [
            {"ts": base_ts, "main_buy_amount": 1000, "main_sell_amount": 0},
            {"ts": base_ts + 60_000, "main_buy_amount": 1000, "main_sell_amount": 0},
            {"ts": base_ts + 120_000, "main_buy_amount": 0, "main_sell_amount": 500},
        ]
        cum_buy, cum_sell, current_ratio, previous_ratio = _cumulative_amounts(bars, "2026-09-14", 9 * 60 + 2)
        self.assertEqual(cum_buy, 2000)
        self.assertEqual(cum_sell, 500)
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

    def _seed_main_force_bars(self, ticker, now):
        base = now.replace(hour=9, minute=0, second=0, microsecond=0)
        bars = []
        minute = 0
        while base + timedelta(minutes=minute) <= now:
            ts = int((base + timedelta(minutes=minute)).timestamp() * 1000)
            bars.append({
                "ts": ts, "main_buy_volume": 40, "main_sell_volume": 0,
                "main_buy_amount": 4_000_000, "main_sell_amount": 0,
                "main_force_available": True,
            })
            minute += 1
        save_main_force_bars(ticker, "1m", bars)

    def test_all_four_conditions_pass_returns_buy_signal(self):
        now = datetime(2026, 9, 14, 9, 35, tzinfo=TW_TZ)  # 週一，09:30-09:59 門檻70%
        self._seed_main_force_bars("2330", now)
        row = {"ticker": "2330", "name": "台積電", "previous_estimated_sell_pressure": 200_000_000.0}
        bars_5m = [
            {"ts": int(now.replace(hour=9, minute=0).timestamp() * 1000), "open": 95, "high": 100, "low": 95, "close": 98, "volume": 1000},
            {"ts": int(now.timestamp() * 1000), "open": 98, "high": 112, "low": 97, "close": 110, "volume": 1000},
        ]
        with patch("four_gate_signals.get_resilient_stock_bars", return_value={"bars": bars_5m}):
            signal = evaluate_ticker(service=object(), hub=FakeHub(), row=row, now=now)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["kind"], "fourGateBuy")
        self.assertEqual(signal["ticker"], "2330")

    def test_previous_pressure_below_one_hundred_million_blocks_signal(self):
        now = datetime(2026, 9, 14, 9, 35, tzinfo=TW_TZ)
        self._seed_main_force_bars("2330", now)
        row = {"ticker": "2330", "name": "台積電", "previous_estimated_sell_pressure": 99_000_000.0}
        signal = evaluate_ticker(service=object(), hub=FakeHub(), row=row, now=now)
        self.assertIsNone(signal)

    def test_outside_trading_window_blocks_signal(self):
        now = datetime(2026, 9, 14, 8, 0, tzinfo=TW_TZ)
        row = {"ticker": "2330", "name": "台積電", "previous_estimated_sell_pressure": 200_000_000.0}
        signal = evaluate_ticker(service=object(), hub=FakeHub(), row=row, now=now)
        self.assertIsNone(signal)

    def test_amount_below_time_tiered_threshold_blocks_signal(self):
        # 09:35 屬於 70% 門檻；只餵5分鐘的量（20,000,000），遠低於 200,000,000*0.70。
        now = datetime(2026, 9, 14, 9, 5, tzinfo=TW_TZ)
        self._seed_main_force_bars("2330", now)
        row = {"ticker": "2330", "name": "台積電", "previous_estimated_sell_pressure": 200_000_000.0}
        bars_5m = [
            {"ts": int(now.replace(hour=9, minute=0).timestamp() * 1000), "open": 95, "high": 100, "low": 95, "close": 98, "volume": 1000},
        ]
        with patch("four_gate_signals.get_resilient_stock_bars", return_value={"bars": bars_5m}):
            signal = evaluate_ticker(service=object(), hub=FakeHub(), row=row, now=now)
        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main()
