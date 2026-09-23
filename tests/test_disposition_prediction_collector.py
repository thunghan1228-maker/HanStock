"""disposition_prediction_collector.py：今天bars_1d還沒寫好就等待、今天已經跑過就
跳過、只有真的有today's bars才會呼叫run_universe_for_date、Phase 2(FinMind基本面
收集+組裝)會在run_universe_for_date之前跑且結果會傳進去。"""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

import disposition_prediction_collector as collector
from otc_index import TW_TZ

_NO_FUNDAMENTALS = {"status": "skipped", "reason": "FINMIND_TOKEN 未設定"}


class DispositionPredictionCollectorTests(unittest.TestCase):
    def setUp(self):
        collector._last_run_date = None

    def test_waits_when_todays_bars_not_ready(self):
        with patch.object(collector, "daily_bars_storage_status", return_value={"lastTradeDate": "2026-09-22"}):
            result = collector.collect_once(now=datetime(2026, 9, 23, 14, 0, tzinfo=TW_TZ))
        self.assertEqual(result["status"], "waiting")

    def test_runs_once_when_bars_ready(self):
        with (
            patch.object(collector, "daily_bars_storage_status", return_value={"lastTradeDate": "2026-09-23"}),
            patch.object(collector, "official_group_codes", return_value={"2330"}),
            patch.object(collector, "collect_finmind_fundamentals", return_value=_NO_FUNDAMENTALS) as fetch_mock,
            patch.object(collector, "build_fundamentals_by_code", return_value={"2330": {}}) as build_mock,
            patch.object(collector, "run_universe_for_date", return_value={"2330": []}) as run_mock,
        ):
            result = collector.collect_once(now=datetime(2026, 9, 23, 14, 0, tzinfo=TW_TZ))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stockCount"], 1)
        self.assertEqual(result["fundamentals"], _NO_FUNDAMENTALS)
        fetch_mock.assert_called_once_with("2026-09-23", ["2330"])
        build_mock.assert_called_once_with("2026-09-23", {"2330"})
        run_mock.assert_called_once_with("2026-09-23", codes={"2330"}, fundamentals_by_code={"2330": {}})

    def test_skips_second_call_same_day(self):
        with (
            patch.object(collector, "daily_bars_storage_status", return_value={"lastTradeDate": "2026-09-23"}),
            patch.object(collector, "collect_finmind_fundamentals", return_value=_NO_FUNDAMENTALS),
            patch.object(collector, "build_fundamentals_by_code", return_value={}),
            patch.object(collector, "run_universe_for_date", return_value={}) as run_mock,
        ):
            first = collector.collect_once(now=datetime(2026, 9, 23, 14, 0, tzinfo=TW_TZ))
            second = collector.collect_once(now=datetime(2026, 9, 23, 14, 30, tzinfo=TW_TZ))
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "skipped")
        run_mock.assert_called_once()

    def test_runs_again_on_new_day(self):
        with (
            patch.object(collector, "daily_bars_storage_status", return_value={"lastTradeDate": "2026-09-23"}),
            patch.object(collector, "collect_finmind_fundamentals", return_value=_NO_FUNDAMENTALS),
            patch.object(collector, "build_fundamentals_by_code", return_value={}),
            patch.object(collector, "run_universe_for_date", return_value={}),
        ):
            collector.collect_once(now=datetime(2026, 9, 23, 14, 0, tzinfo=TW_TZ))
        with (
            patch.object(collector, "daily_bars_storage_status", return_value={"lastTradeDate": "2026-09-24"}),
            patch.object(collector, "collect_finmind_fundamentals", return_value=_NO_FUNDAMENTALS),
            patch.object(collector, "build_fundamentals_by_code", return_value={}),
            patch.object(collector, "run_universe_for_date", return_value={}) as run_mock,
        ):
            result = collector.collect_once(now=datetime(2026, 9, 24, 14, 0, tzinfo=TW_TZ))
        self.assertEqual(result["status"], "ok")
        run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
