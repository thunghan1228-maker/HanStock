import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from intraday_signal_store import load_signals_for_ticker, save_intraday_signals


class LoadSignalsForTickerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_returns_only_requested_ticker_and_date_sorted_ascending(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-18", "ticker": "2330", "kind": "watch12short",
             "label": "注意12空", "barTs": 3_000, "price": 100.0},
            {"tradeDate": "2026-09-18", "ticker": "2330", "kind": "firstCross905High",
             "label": "首次過905高", "barTs": 1_000, "price": 101.0},
            {"tradeDate": "2026-09-18", "ticker": "2330", "kind": "crossUpPrevHigh",
             "label": "第1次站上昨日高", "barTs": 2_000, "price": 102.0, "note": "第1次"},
            {"tradeDate": "2026-09-18", "ticker": "9999", "kind": "watch12short",
             "label": "注意12空", "barTs": 1_500, "price": 50.0},
            {"tradeDate": "2026-09-17", "ticker": "2330", "kind": "watch12short",
             "label": "注意12空", "barTs": 500, "price": 90.0},
        ])
        signals = load_signals_for_ticker("2330", "2026-09-18")
        self.assertEqual([s["barTs"] for s in signals], [1_000, 2_000, 3_000])
        self.assertEqual([s["kind"] for s in signals], [
            "firstCross905High", "crossUpPrevHigh", "watch12short",
        ])
        self.assertTrue(all(s["ticker"] == "2330" for s in signals))

    def test_returns_empty_list_when_no_signals_for_ticker(self):
        signals = load_signals_for_ticker("1234", "2026-09-18")
        self.assertEqual(signals, [])

    def test_trade_date_is_optional_and_returns_all_dates_when_omitted(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-17", "ticker": "2330", "kind": "watch12short",
             "label": "注意12空", "barTs": 500, "price": 90.0},
            {"tradeDate": "2026-09-18", "ticker": "2330", "kind": "short12",
             "label": "12空", "barTs": 3_000, "price": 100.0},
        ])
        signals = load_signals_for_ticker("2330")
        self.assertEqual([s["tradeDate"] for s in signals], ["2026-09-17", "2026-09-18"])

    def test_since_ts_filters_out_earlier_bars(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-18", "ticker": "2330", "kind": "watch12short",
             "label": "注意12空", "barTs": 1_000, "price": 90.0},
            {"tradeDate": "2026-09-18", "ticker": "2330", "kind": "short12",
             "label": "12空", "barTs": 3_000, "price": 100.0},
        ])
        signals = load_signals_for_ticker("2330", "2026-09-18", since_ts=2_000)
        self.assertEqual([s["kind"] for s in signals], ["short12"])

    def test_ticker_matching_is_case_sensitive_caller_must_normalize(self):
        # load_signals_for_ticker本身不做正規化；呼叫端(persistent_app的端點)
        # 已經用_normalize_stock_code轉大寫，這裡確認的是儲存層的真實行為，
        # 不是幫呼叫端補容錯。
        save_intraday_signals([
            {"tradeDate": "2026-09-18", "ticker": "00631L", "kind": "ma520Up",
             "label": "五二零上", "barTs": 1_000, "price": 20.0},
        ])
        self.assertEqual(len(load_signals_for_ticker("00631L", "2026-09-18")), 1)
        self.assertEqual(len(load_signals_for_ticker("00631l", "2026-09-18")), 0)


if __name__ == "__main__":
    unittest.main()
