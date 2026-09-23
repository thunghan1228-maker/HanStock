"""disposition_prediction.py：ClauseInputs組裝(市場統計+同類平均+Phase2欄位合併)、
disposition_clause_log存取、以及第六條累積規則(連續3天款一／連續5天款一到七／10天內6次／
30天內12次)的視窗判定。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database
from database import get_connection, initialize_database
from disposition_prediction import (
    build_clause_inputs,
    check_disposition_trigger,
    official_group_codes,
    run_universe_for_date,
    save_clause_results,
)
from disposition_market_stats import MarketSnapshot, StockPriceMetrics
from disposition_rules import CHECKERS, ClauseResult


def _business_days_ending(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _insert_bars(connection, code: str, dates: list[date], closes: list[float]) -> None:
    rows = [
        (code, d.isoformat() + "T00:00:00+00:00", closes[idx], closes[idx] + 1, closes[idx] - 1, closes[idx], 1000.0)
        for idx, d in enumerate(dates)
    ]
    connection.executemany(
        """
        INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_code, bar_time) DO UPDATE SET close = excluded.close
        """,
        rows,
    )


def _all_clauses_result(fired_clauses: set[str]) -> list[ClauseResult]:
    return [ClauseResult(clause, clause in fired_clauses, "測試假資料") for clause in CHECKERS]


class OfficialGroupCodesTests(unittest.TestCase):
    def test_returns_nonempty_set_of_codes(self):
        codes = official_group_codes()
        self.assertGreater(len(codes), 0)
        self.assertTrue(all(isinstance(c, str) for c in codes))


class BuildClauseInputsTests(unittest.TestCase):
    def test_merges_snapshot_and_fundamentals(self):
        metrics = StockPriceMetrics(code="2330", close=500.0, volume=1000.0, change_6d_pct=10.0)
        snapshot = MarketSnapshot(
            trade_date="2026-09-23", metrics_by_code={"2330": metrics},
            peer_avg={"change_6d_pct": 2.0}, industry_avg={"半導體": {"change_6d_pct": 3.0}},
        )
        inputs = build_clause_inputs(
            "2330", snapshot, industry="半導體",
            fundamentals={"pe_ratio": 20.0, "turnover_pct": 5.0},
        )
        self.assertIsNotNone(inputs)
        self.assertEqual(inputs.close, 500.0)
        self.assertEqual(inputs.change_6d_pct, 10.0)
        self.assertEqual(inputs.change_6d_peer_avg_pct, 2.0)
        self.assertEqual(inputs.change_6d_industry_avg_pct, 3.0)
        self.assertEqual(inputs.pe_ratio, 20.0)
        self.assertEqual(inputs.turnover_pct, 5.0)
        self.assertIsNone(inputs.pbr)  # 沒給的Phase2欄位維持None，不是0

    def test_returns_none_when_stock_missing_from_snapshot(self):
        snapshot = MarketSnapshot(trade_date="2026-09-23", metrics_by_code={}, peer_avg={}, industry_avg={})
        self.assertIsNone(build_clause_inputs("2330", snapshot))


class RunUniverseForDateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.today = date(2026, 9, 23)
        self.dates = _business_days_ending(self.today, 6)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_runs_and_persists_for_target_codes_only(self):
        with get_connection() as connection:
            _insert_bars(connection, "2330", self.dates, [100.0] * 5 + [140.0])  # +40% 6日
            _insert_bars(connection, "9999", self.dates, [100.0] * 6)  # 不在target裡

        results = run_universe_for_date(self.today.isoformat(), codes={"2330"})
        self.assertIn("2330", results)
        self.assertNotIn("9999", results)

        with get_connection() as connection:
            rows = connection.execute(
                "SELECT clause, fired FROM disposition_clause_log WHERE stock_code = ?", ("2330",)
            ).fetchall()
        self.assertEqual(len(rows), len(CHECKERS))
        fired_clauses = {r["clause"] for r in rows if r["fired"]}
        self.assertIn("一", fired_clauses)  # 40%累積漲跌、全體平均只有這一檔差幅一定達標

    def test_skips_codes_with_no_snapshot_data(self):
        results = run_universe_for_date(self.today.isoformat(), codes={"0000"})
        self.assertEqual(results, {})


class SaveAndCheckDispositionTriggerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.today = date(2026, 9, 23)  # 週三

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed(self, code: str, date_to_clauses: dict[date, set[str]]) -> None:
        for trade_date, clauses in date_to_clauses.items():
            save_clause_results(trade_date.isoformat(), code, _all_clauses_result(clauses))

    def test_no_history_means_no_trigger(self):
        status = check_disposition_trigger("2330", self.today.isoformat())
        self.assertIsNone(status.trigger_path)
        self.assertIsNone(status.predicted_duration_business_days)

    def test_three_consecutive_clause_1_days_triggers_path_1(self):
        days = _business_days_ending(self.today, 3)
        self._seed("2330", {d: {"一"} for d in days})
        status = check_disposition_trigger("2330", self.today.isoformat())
        self.assertEqual(status.trigger_path, "連續3個營業日依第一款發布注意")
        self.assertEqual(status.predicted_duration_business_days, 5)
        self.assertIsNotNone(status.duration_caveat)

    def test_two_consecutive_clause_1_days_does_not_trigger_path_1(self):
        days = _business_days_ending(self.today, 2)
        self._seed("2330", {d: {"一"} for d in days})
        status = check_disposition_trigger("2330", self.today.isoformat())
        self.assertNotEqual(status.trigger_path, "連續3個營業日依第一款發布注意")

    def test_break_in_sequence_resets_consecutive_count(self):
        """今天、前天都中款一，但大前天(3天前)沒中——不是連續3天，不該觸發路徑一。"""
        days = _business_days_ending(self.today, 3)
        self._seed("2330", {days[0]: set(), days[1]: {"一"}, days[2]: {"一"}})
        status = check_disposition_trigger("2330", self.today.isoformat())
        self.assertNotEqual(status.trigger_path, "連續3個營業日依第一款發布注意")

    def test_five_consecutive_days_mixed_clauses_triggers_path_2(self):
        days = _business_days_ending(self.today, 5)
        clauses_per_day = [{"一"}, {"二"}, {"三"}, {"四"}, {"六"}]
        self._seed("2330", dict(zip(days, clauses_per_day)))
        status = check_disposition_trigger("2330", self.today.isoformat())
        self.assertIn("連續5個營業日", status.trigger_path)

    def test_ten_day_window_with_six_hits_triggers_path_3(self):
        """今天(最新一天)故意不命中，擋掉路徑一/二(那兩條只看「從今天往回算連續幾天」)；
        6次命中都放在較舊的日子，純粹測「10天窗口內累積6次」這條路徑。"""
        days = _business_days_ending(self.today, 10)
        date_to_clauses = {d: set() for d in days}
        for d in days[:6]:  # 最舊的6天命中，最新的4天(含今天)不命中
            date_to_clauses[d] = {"二"}
        self._seed("2330", date_to_clauses)
        status = check_disposition_trigger("2330", self.today.isoformat())
        self.assertIn("最近10個營業日內有6天", status.trigger_path)

    def test_five_hits_in_ten_days_does_not_trigger(self):
        days = _business_days_ending(self.today, 10)
        date_to_clauses = {d: set() for d in days}
        for d in days[:5]:  # 只有5天命中，且不含今天
            date_to_clauses[d] = {"二"}
        self._seed("2330", date_to_clauses)
        status = check_disposition_trigger("2330", self.today.isoformat())
        self.assertIsNone(status.trigger_path)

    def test_thirty_day_window_with_twelve_hits_triggers_path_4(self):
        """12次命中全部放在最舊的12天，最新的18天(含今天)都不命中，確保不會被路徑一/二/三
        先攔截，單獨測「30天窗口內累積12次」這條路徑。"""
        days = _business_days_ending(self.today, 30)
        date_to_clauses = {d: set() for d in days}
        for d in days[:12]:
            date_to_clauses[d] = {"七"}
        self._seed("2330", date_to_clauses)
        status = check_disposition_trigger("2330", self.today.isoformat())
        self.assertIn("最近30個營業日內有12天", status.trigger_path)

    def test_clause_9_and_10_do_not_count_toward_accumulation(self):
        """第九/十款不在「第一款至第八款」的處置累積基數裡（官方原文只算一到八款），
        就算連續多天觸發九/十也不該被算進處置累積。"""
        days = _business_days_ending(self.today, 5)
        self._seed("2330", {d: {"九", "十"} for d in days})
        status = check_disposition_trigger("2330", self.today.isoformat())
        self.assertIsNone(status.trigger_path)


if __name__ == "__main__":
    unittest.main()
