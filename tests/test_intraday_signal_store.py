import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
from intraday_signal_store import (
    delete_kline_signals_for_ticker,
    find_out_of_session_kline_signals,
    load_latest_signals,
    load_signals_for_ticker,
    purge_out_of_session_kline_signals,
    save_intraday_signals,
)

TW_TZ = timezone(timedelta(hours=8))


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


class OutOfSessionKlineSignalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _ts(hour, minute, day=18):
        return int(datetime(2026, 9, day, hour, minute, tzinfo=TW_TZ).timestamp() * 1000)

    def test_finds_pre_market_and_after_hours_kline_rows_but_not_in_session_ones(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-18", "ticker": "1101", "kind": "watch12short",
             "label": "注意12空", "barTs": self._ts(8, 50), "price": 10.0},
            {"tradeDate": "2026-09-18", "ticker": "1102", "kind": "combo12Bull",
             "label": "1+2多", "barTs": self._ts(14, 0), "price": 20.0},
            {"tradeDate": "2026-09-18", "ticker": "1103", "kind": "ma520Up",
             "label": "五二零上", "barTs": self._ts(10, 30), "price": 30.0},
        ])
        rows = find_out_of_session_kline_signals("2026-09-18")
        self.assertEqual([r["ticker"] for r in rows], ["1101", "1102"])

    def test_boundary_0900_is_in_session_and_1330_is_out_of_session(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-18", "ticker": "1104", "kind": "watch12short",
             "label": "注意12空", "barTs": self._ts(9, 0), "price": 10.0},
            {"tradeDate": "2026-09-18", "ticker": "1105", "kind": "watch12short",
             "label": "注意12空", "barTs": self._ts(13, 30), "price": 10.0},
        ])
        rows = find_out_of_session_kline_signals("2026-09-18")
        self.assertEqual([r["ticker"] for r in rows], ["1105"])

    def test_ignores_non_kline_signal_families_even_if_out_of_session(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-18", "ticker": "1106", "kind": "instantLargeBuy",
             "label": "盤中特大買單", "barTs": self._ts(8, 50), "price": 10.0},
        ])
        self.assertEqual(find_out_of_session_kline_signals("2026-09-18"), [])

    def test_only_returns_requested_trade_date(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-17", "ticker": "1107", "kind": "watch12short",
             "label": "注意12空", "barTs": self._ts(8, 50, day=17), "price": 10.0},
            {"tradeDate": "2026-09-18", "ticker": "1108", "kind": "watch12short",
             "label": "注意12空", "barTs": self._ts(8, 50), "price": 10.0},
        ])
        rows = find_out_of_session_kline_signals("2026-09-18")
        self.assertEqual([r["ticker"] for r in rows], ["1108"])

    def test_purge_deletes_only_out_of_session_kline_rows_and_returns_count(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-18", "ticker": "1109", "kind": "watch12short",
             "label": "注意12空", "barTs": self._ts(8, 50), "price": 10.0},
            {"tradeDate": "2026-09-18", "ticker": "1110", "kind": "combo12Bull",
             "label": "1+2多", "barTs": self._ts(14, 0), "price": 20.0},
            {"tradeDate": "2026-09-18", "ticker": "1111", "kind": "ma520Up",
             "label": "五二零上", "barTs": self._ts(10, 30), "price": 30.0},
            {"tradeDate": "2026-09-18", "ticker": "1112", "kind": "instantLargeBuy",
             "label": "盤中特大買單", "barTs": self._ts(8, 50), "price": 40.0},
        ])
        deleted = purge_out_of_session_kline_signals("2026-09-18")
        self.assertEqual(deleted, 2)
        self.assertEqual(len(load_signals_for_ticker("1109", "2026-09-18")), 0)
        self.assertEqual(len(load_signals_for_ticker("1110", "2026-09-18")), 0)
        self.assertEqual(len(load_signals_for_ticker("1111", "2026-09-18")), 1)
        self.assertEqual(len(load_signals_for_ticker("1112", "2026-09-18")), 1)

    def test_purge_only_affects_requested_trade_date(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-17", "ticker": "1113", "kind": "watch12short",
             "label": "注意12空", "barTs": self._ts(8, 50, day=17), "price": 10.0},
            {"tradeDate": "2026-09-18", "ticker": "1114", "kind": "watch12short",
             "label": "注意12空", "barTs": self._ts(8, 50), "price": 10.0},
        ])
        deleted = purge_out_of_session_kline_signals("2026-09-18")
        self.assertEqual(deleted, 1)
        self.assertEqual(len(load_signals_for_ticker("1113", "2026-09-17")), 1)


class LoadLatestSignalsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_returns_full_day_not_just_the_most_recent_200_rows(self):
        # 活躍盤勢一天全部kind混在一起可能遠超過200筆(跟只看單一kind的
        # load_latest_signals_by_kind不同)。改之前limit會被再夾到200，
        # 只回傳ORDER BY bar_ts DESC的最新200筆，早盤那些訊號不是沒發生，
        # 是直接被這個上限砍掉、看起來像消失了。
        rows = [
            {"tradeDate": "2026-09-21", "ticker": "TEST", "kind": "watch12short",
             "label": "注意12空", "barTs": 1_000 + i, "price": 100.0}
            for i in range(250)
        ]
        save_intraday_signals(rows)
        signals = load_latest_signals("2026-09-21", limit=500, include_chart_kinds=True)
        self.assertEqual(len(signals), 250)
        self.assertEqual(min(s["barTs"] for s in signals), 1_000)

    def test_flip_signals_dedupe_within_five_minutes_but_allow_a_later_repeat(self):
        # 主力累計翻多空一天可以發多次（另一台工具 3532 當天 11:38、11:57 各一次），
        # 但同一次翻轉在 5 分鐘內重複算出來的不能再入庫。
        base = 1_790_000_000_000
        rows = [
            {"tradeDate": "2026-09-22", "ticker": "3532", "kind": "mainForceFlipBull",
             "label": "主力累計強勢翻多", "barTs": base, "price": 451.5, "note": "第一次"},
            {"tradeDate": "2026-09-22", "ticker": "3532", "kind": "mainForceFlipBull",
             "label": "主力累計強勢翻多", "barTs": base + 3 * 60_000, "price": 452.0, "note": "3 分鐘後重複"},
            {"tradeDate": "2026-09-22", "ticker": "3532", "kind": "mainForceFlipBull",
             "label": "主力累計強勢翻多", "barTs": base + 19 * 60_000, "price": 447.0, "note": "19 分鐘後再翻一次"},
        ]
        inserted = save_intraday_signals(rows)
        self.assertEqual([r["note"] for r in inserted], ["第一次", "19 分鐘後再翻一次"])
        self.assertEqual(len(load_latest_signals("2026-09-22", limit=50)), 2)

    def test_chart_only_kline_kinds_are_left_out_so_early_signals_survive_the_limit(self):
        # 2026-09-22 正式環境：一天的圖表用 5 分 K 訊號超過 5000 筆，09 點多那批主力翻多空
        # 全被 ORDER BY bar_ts DESC LIMIT 砍掉，前端只看得到 12:19 的那一筆。訊號中心根本
        # 不顯示圖表用的 kind，當日總表預設就不要回它們。
        chart_rows = [
            {"tradeDate": "2026-09-22", "ticker": "TEST", "kind": "watch12short",
             "label": "注意12空", "barTs": 2_000 + i, "price": 100.0}
            for i in range(300)
        ]
        early = [
            {"tradeDate": "2026-09-22", "ticker": "3532", "kind": "mainForceFlipBull",
             "label": "主力累計強勢翻多", "barTs": 1_000, "price": 451.5, "note": "A～D同步濾網"},
            {"tradeDate": "2026-09-22", "ticker": "2330", "kind": "combo12Bull",
             "label": "1+2多", "barTs": 1_500, "price": 1000.0},
        ]
        save_intraday_signals(chart_rows + early)

        signals = load_latest_signals("2026-09-22", limit=100)
        self.assertEqual([s["kind"] for s in signals], ["combo12Bull", "mainForceFlipBull"])

        full = load_latest_signals("2026-09-22", limit=100, include_chart_kinds=True)
        self.assertEqual(len(full), 100)
        self.assertTrue(all(s["kind"] == "watch12short" for s in full))  # 帶完整資料時早盤那兩筆就被上限吃掉


class DeleteKlineSignalsForTickerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_deletes_only_kline_family_rows_for_that_ticker_and_date(self):
        save_intraday_signals([
            {"tradeDate": "2026-09-21", "ticker": "4979", "kind": "combo12Bull",
             "label": "1+2多", "barTs": 1_000, "price": 100.0},
            {"tradeDate": "2026-09-21", "ticker": "4979", "kind": "crossUpPrevHigh",
             "label": "第1次站上昨日高", "barTs": 900, "price": 99.0, "note": "第1次"},
            # 不同家族，不該被動到
            {"tradeDate": "2026-09-21", "ticker": "4979", "kind": "instantLargeBuy",
             "label": "瞬間大單", "barTs": 1_000, "price": 100.0},
            # 不同股票，不該被動到
            {"tradeDate": "2026-09-21", "ticker": "3050", "kind": "combo12Bull",
             "label": "1+2多", "barTs": 1_000, "price": 50.0},
            # 不同日期，不該被動到
            {"tradeDate": "2026-09-20", "ticker": "4979", "kind": "combo12Bull",
             "label": "1+2多", "barTs": 1_000, "price": 100.0},
        ])
        deleted = delete_kline_signals_for_ticker("2026-09-21", "4979")
        self.assertEqual(deleted, 2)
        self.assertEqual(len(load_signals_for_ticker("4979", "2026-09-21")), 1)  # 只剩instantLargeBuy
        self.assertEqual(load_signals_for_ticker("4979", "2026-09-21")[0]["kind"], "instantLargeBuy")
        self.assertEqual(len(load_signals_for_ticker("3050", "2026-09-21")), 1)
        self.assertEqual(len(load_signals_for_ticker("4979", "2026-09-20")), 1)

    def test_returns_zero_when_nothing_to_delete(self):
        self.assertEqual(delete_kline_signals_for_ticker("2026-09-21", "9999"), 0)


if __name__ == "__main__":
    unittest.main()
