"""disposition_gap_prediction.py：連續2天(不多不少)命中第一款才算「還差1次」、明天
收盤價門檻反推(32%/25%+價差兩個子條件挑較容易達成的、正確處理漲跌方向)、差幅門檻
過濾掉不會用到的子條件、easy標籤跟人類可讀說明。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database
from database import get_connection, initialize_database
from disposition_gap_prediction import (
    _build_prediction,
    _clause_1_threshold,
    build_gap_predictions,
    find_path1_near_miss,
)
from disposition_prediction import save_clause_results
from disposition_rules import CHECKERS, ClauseResult


def _business_days_ending(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _all_clauses_result(fired_clauses: set[str]) -> list[ClauseResult]:
    return [ClauseResult(clause, clause in fired_clauses, "測試假資料") for clause in CHECKERS]


class Clause1ThresholdTests(unittest.TestCase):
    def test_up_direction_small_price_picks_subcondition_1(self):
        closes = [100.0, 100.0, 100.0, 100.0, 100.0]  # closes[-5]=ref=100
        result = _clause_1_threshold(closes, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNotNone(result)
        threshold, direction, ref = result
        self.assertEqual(direction, "up")
        self.assertAlmostEqual(ref, 100.0)
        self.assertAlmostEqual(threshold, 132.0)  # 100*1.32 < max(100*1.25, 100+50)=150

    def test_up_direction_large_price_picks_subcondition_2(self):
        closes = [1000.0, 1000.0, 1000.0, 1000.0, 1000.0]
        result = _clause_1_threshold(closes, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        threshold, direction, _ref = result
        self.assertAlmostEqual(threshold, 1250.0)  # max(1000*1.25, 1000+50)=1250 < 1000*1.32=1320

    def test_down_direction_mirrors_up(self):
        closes = [100.0, 100.0, 100.0, 100.0, 100.0]
        result = _clause_1_threshold(closes, today_change_6d_pct=-10.0, peer_avg_6d_pct=None)
        threshold, direction, ref = result
        self.assertEqual(direction, "down")
        self.assertAlmostEqual(threshold, 68.0)  # max(68.0, min(75,50)=50) = 68.0，跌得少的那個較容易達成

    def test_returns_none_with_fewer_than_5_closes(self):
        result = _clause_1_threshold([100.0, 100.0, 100.0], today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNone(result)

    def test_diff_filter_excludes_subcondition_1_when_peer_avg_too_close(self):
        """peer_avg=45時，32%門檻跟peer_avg差幅只有13(<20，子條件一不適用)，
        但25%門檻差幅剛好20(子條件二仍適用)——確認只用子條件二算出來的門檻。"""
        closes = [100.0, 100.0, 100.0, 100.0, 100.0]
        result = _clause_1_threshold(closes, today_change_6d_pct=10.0, peer_avg_6d_pct=45.0)
        threshold, _direction, _ref = result
        self.assertAlmostEqual(threshold, 150.0)  # 只剩子條件二：max(125, 150)=150

    def test_returns_none_when_both_subconditions_filtered_out(self):
        closes = [100.0, 100.0, 100.0, 100.0, 100.0]
        result = _clause_1_threshold(closes, today_change_6d_pct=10.0, peer_avg_6d_pct=30.0)
        self.assertIsNone(result)

    def test_zero_reference_close_returns_none(self):
        result = _clause_1_threshold([0.0, 0.0, 0.0, 0.0, 0.0], today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNone(result)


class BuildPredictionTests(unittest.TestCase):
    def test_easy_label_when_threshold_at_or_below_today_close(self):
        closes = [100.0] * 5
        prediction = _build_prediction("2330", closes, today_close=140.0, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNotNone(prediction)
        self.assertTrue(prediction.easy)
        self.assertIn("收平盤或收紅", prediction.detail)
        self.assertEqual(prediction.direction, "up")

    def test_specific_pct_label_when_real_move_needed(self):
        closes = [100.0] * 5
        prediction = _build_prediction("2330", closes, today_close=100.0, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertFalse(prediction.easy)
        self.assertIn("漲幅", prediction.detail)
        self.assertAlmostEqual(prediction.change_pct_from_today, 32.0)  # threshold132 vs today_close100

    def test_none_when_threshold_computation_fails(self):
        prediction = _build_prediction("2330", [100.0, 100.0], today_close=100.0, today_change_6d_pct=10.0, peer_avg_6d_pct=None)
        self.assertIsNone(prediction)


class FindPath1NearMissTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.today = date(2026, 9, 23)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed(self, code: str, date_to_clauses: dict[date, set[str]]) -> None:
        for trade_date, clauses in date_to_clauses.items():
            save_clause_results(trade_date.isoformat(), code, _all_clauses_result(clauses))

    def test_finds_stock_with_exactly_2_consecutive_hits(self):
        days = _business_days_ending(self.today, 2)
        self._seed("2330", {d: {"一"} for d in days})
        result = find_path1_near_miss({"2330"}, self.today.isoformat())
        self.assertEqual(result, {"2330"})

    def test_excludes_stock_already_at_3_consecutive_hits(self):
        """已經連續3天，路徑一已經真的觸發了，不是「還差1次」。"""
        days = _business_days_ending(self.today, 3)
        self._seed("2330", {d: {"一"} for d in days})
        result = find_path1_near_miss({"2330"}, self.today.isoformat())
        self.assertEqual(result, set())

    def test_excludes_stock_with_only_1_hit(self):
        days = _business_days_ending(self.today, 2)
        self._seed("2330", {days[0]: set(), days[1]: {"一"}})
        result = find_path1_near_miss({"2330"}, self.today.isoformat())
        self.assertEqual(result, set())

    def test_excludes_stock_where_today_did_not_fire(self):
        """昨天中、今天沒中——不是「連續到今天」，序列已經斷了。"""
        days = _business_days_ending(self.today, 2)
        self._seed("2330", {days[0]: {"一"}, days[1]: set()})
        result = find_path1_near_miss({"2330"}, self.today.isoformat())
        self.assertEqual(result, set())


class BuildGapPredictionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.today = date(2026, 9, 23)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_bars(self, code: str, closes_by_date: dict[date, float]) -> None:
        with get_connection() as connection:
            rows = [
                (code, d.isoformat() + "T00:00:00+00:00", c, c, c, c, 1000.0)
                for d, c in closes_by_date.items()
            ]
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(stock_code, bar_time) DO UPDATE SET close = excluded.close",
                rows,
            )

    def test_empty_when_no_near_miss_stocks(self):
        result = build_gap_predictions(self.today.isoformat(), {"2330"})
        self.assertEqual(result, [])

    def test_end_to_end_produces_prediction_for_near_miss_stock(self):
        days = _business_days_ending(self.today, 2)
        save_clause_results(days[0].isoformat(), "2330", _all_clauses_result({"一"}))
        save_clause_results(days[1].isoformat(), "2330", _all_clauses_result({"一"}))
        # 需要至少6天的bars_1d歷史讓change_6d_pct算得出來，加上明天窗口起點要有closes[-5]。
        history_days = _business_days_ending(self.today, 8)
        closes_by_date = {d: 100.0 + i for i, d in enumerate(history_days)}
        self._seed_bars("2330", closes_by_date)
        result = build_gap_predictions(self.today.isoformat(), {"2330"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "2330")
        self.assertEqual(result[0].clause, "一")

    def test_stock_missing_from_market_snapshot_is_skipped_not_erroring(self):
        days = _business_days_ending(self.today, 2)
        save_clause_results(days[0].isoformat(), "9999", _all_clauses_result({"一"}))
        save_clause_results(days[1].isoformat(), "9999", _all_clauses_result({"一"}))
        # 沒有補bars_1d，snapshot裡不會有這檔的metrics。
        result = build_gap_predictions(self.today.isoformat(), {"9999"})
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
