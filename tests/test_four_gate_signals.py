import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import database
from main_force_store import save_main_force_bars
from four_gate_signals import (
    BUY_KIND,
    BUY_LABEL,
    SELL_KIND,
    SELL_LABEL,
    _minute_net_ratios,
    _price_position,
    _time_window_threshold,
    evaluate_ticker,
    fix_stale_four_gate_labels,
)
from intraday_signal_store import load_latest_signals, save_intraday_signals
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

    def test_time_window_threshold_steps_by_clock(self):
        self.assertEqual(_time_window_threshold(9 * 60), 0.50)
        self.assertEqual(_time_window_threshold(9 * 60 + 29), 0.50)
        self.assertEqual(_time_window_threshold(9 * 60 + 30), 0.70)
        self.assertEqual(_time_window_threshold(9 * 60 + 59), 0.70)
        self.assertEqual(_time_window_threshold(10 * 60), 0.90)
        self.assertEqual(_time_window_threshold(10 * 60 + 59), 0.90)
        self.assertEqual(_time_window_threshold(11 * 60), 1.20)
        self.assertEqual(_time_window_threshold(13 * 60 + 30), 1.20)
        self.assertIsNone(_time_window_threshold(8 * 60 + 59))
        self.assertIsNone(_time_window_threshold(13 * 60 + 31))

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
        database.initialize_database()

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

    def _seed_previous_day_pressure(self, ticker, today, *, net_amount=150_000_000):
        """種前一個交易日（跳過週末）的大單淨買超金額，作為候選門檻用的
        「前日預估隔日賣壓」基準。"""
        prev_date = today - timedelta(days=1)
        while prev_date.weekday() >= 5:
            prev_date -= timedelta(days=1)
        ts = int(prev_date.replace(hour=9, minute=0, second=0, microsecond=0).timestamp() * 1000)
        save_main_force_bars(ticker, "5m", [{
            "ts": ts, "main_buy_volume": 1000, "main_sell_volume": 0,
            "main_buy_amount": net_amount, "main_sell_amount": 0,
            "main_force_available": True,
        }])

    def test_bullish_force_and_price_position_returns_buy_signal(self):
        now = datetime(2026, 9, 14, 9, 35, tzinfo=TW_TZ)  # 週一
        self._seed_previous_day_pressure("2330", now)  # 前日淨買超1.5億，門檻通過
        self._seed_main_force_bars("2330", now)  # 全部是買超 -> 淨額比 100%；累計1.44億≥70%門檻
        bars_5m = [
            {"ts": int(now.replace(hour=9, minute=0).timestamp() * 1000), "open": 95, "high": 100, "low": 95, "close": 98, "volume": 1000},
            {"ts": int(now.timestamp() * 1000), "open": 98, "high": 112, "low": 97, "close": 110, "volume": 1000},
        ]
        with patch("four_gate_signals.get_resilient_stock_bars", return_value={"bars": bars_5m}):
            signal = evaluate_ticker(service=object(), hub=FakeHub(), ticker="2330", now=now)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["kind"], "fourGateBuy")
        self.assertEqual(signal["ticker"], "2330")

    def test_missing_previous_day_pressure_blocks_signal(self):
        # 沒有前一交易日的大單資料，候選門檻（前日預估隔日賣壓>1億）無法通過。
        now = datetime(2026, 9, 14, 9, 35, tzinfo=TW_TZ)
        self._seed_main_force_bars("2330", now)  # 今日條件本身都符合
        bars_5m = [
            {"ts": int(now.replace(hour=9, minute=0).timestamp() * 1000), "open": 95, "high": 100, "low": 95, "close": 98, "volume": 1000},
            {"ts": int(now.timestamp() * 1000), "open": 98, "high": 112, "low": 97, "close": 110, "volume": 1000},
        ]
        with patch("four_gate_signals.get_resilient_stock_bars", return_value={"bars": bars_5m}):
            signal = evaluate_ticker(service=object(), hub=FakeHub(), ticker="2330", now=now)
        self.assertIsNone(signal)

    def test_weak_force_ratio_blocks_signal(self):
        # 分時資金強度與價格都通過，但當分鐘淨額比僅約1.7%，沒有到50%門檻。
        now = datetime(2026, 9, 14, 9, 35, tzinfo=TW_TZ)
        self._seed_previous_day_pressure("2330", now, net_amount=140_000_000)
        self._seed_main_force_bars("2330", now, buy=3_000_000, sell=2_900_000)
        bars_5m = [
            {"ts": int(now.replace(hour=9, minute=0).timestamp() * 1000), "open": 95, "high": 100, "low": 95, "close": 98, "volume": 1000},
            {"ts": int(now.timestamp() * 1000), "open": 98, "high": 112, "low": 97, "close": 110, "volume": 1000},
        ]
        with patch("four_gate_signals.get_resilient_stock_bars", return_value={"bars": bars_5m}):
            signal = evaluate_ticker(service=object(), hub=FakeHub(), ticker="2330", now=now)
        self.assertIsNone(signal)

    def test_price_not_breaking_first_bar_high_blocks_signal(self):
        now = datetime(2026, 9, 14, 9, 35, tzinfo=TW_TZ)
        self._seed_previous_day_pressure("2330", now)
        self._seed_main_force_bars("2330", now)  # 分時強度、主力淨額比條件都通過
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


class FixStaleFourGateLabelsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_rewrites_stale_labels_and_is_idempotent(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-18", "ticker": "1582", "kind": BUY_KIND,
             "label": "精選（強多）", "barTs": 1_000, "price": 70.9},
            {"tradeDate": "2026-09-18", "ticker": "6873", "kind": SELL_KIND,
             "label": "精選（強空）", "barTs": 1_001, "price": 48.85},
            {"tradeDate": "2026-09-18", "ticker": "9999", "kind": BUY_KIND,
             "label": BUY_LABEL, "barTs": 1_002, "price": 10.0},
        ])
        updated = fix_stale_four_gate_labels()
        self.assertEqual(updated, 2)  # 第三筆已經是正確文字，不算在內
        signals = load_latest_signals("2026-09-18", limit=10)
        labels = {s["ticker"]: s["label"] for s in signals}
        self.assertEqual(labels["1582"], BUY_LABEL)
        self.assertEqual(labels["6873"], SELL_LABEL)
        self.assertEqual(labels["9999"], BUY_LABEL)
        # 重複呼叫：全部都已經是最新文字，不該再有任何更新。
        self.assertEqual(fix_stale_four_gate_labels(), 0)

    def test_does_not_touch_unrelated_signal_kinds(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-18", "ticker": "2344", "kind": "instantLargeBuy",
             "label": "瞬間大單連續敲進", "barTs": 2_000, "price": 100.0},
        ])
        self.assertEqual(fix_stale_four_gate_labels(), 0)
        signals = load_latest_signals("2026-09-18", limit=10)
        self.assertEqual(signals[0]["label"], "瞬間大單連續敲進")


if __name__ == "__main__":
    unittest.main()
