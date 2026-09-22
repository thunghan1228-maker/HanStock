import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import kline_signal_backfill_collector as collector

TW_TZ = timezone(timedelta(hours=8))


class KlineSignalBackfillCollectorTests(unittest.TestCase):
    def setUp(self):
        collector._last_backfilled_date = None

    def tearDown(self):
        collector._last_backfilled_date = None

    def test_skips_on_weekend(self):
        saturday = datetime(2026, 9, 19, 15, 0, tzinfo=TW_TZ)
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            result = collector.collect_once()
        self.assertEqual(result["skipped"], "weekend")

    def test_skips_before_settlement(self):
        before_settle = datetime(2026, 9, 18, 13, 30, tzinfo=TW_TZ)  # Friday
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = before_settle
            result = collector.collect_once()
        self.assertEqual(result["skipped"], "before_settlement")

    def test_skips_when_already_backfilled_today(self):
        collector._last_backfilled_date = "2026-09-18"
        after_settle = datetime(2026, 9, 18, 14, 0, tzinfo=TW_TZ)  # Friday
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = after_settle
            result = collector.collect_once()
        self.assertEqual(result["skipped"], "already_backfilled_today")

    def test_skips_when_backfill_already_running_elsewhere(self):
        # 使用者自己手動打/api/hub/kline-signals/backfill-today、或上一輪
        # 還沒結束時，start_kline_signal_backfill_today本身就會回傳
        # started=False，這裡不該再啟動一次重複的背景執行緒。
        after_settle = datetime(2026, 9, 18, 14, 0, tzinfo=TW_TZ)  # Friday
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = after_settle
            with patch.object(
                collector, "start_kline_signal_backfill_today",
                return_value={"started": False, "reason": "already_running"},
            ) as mock_start:
                result = collector.collect_once()
        mock_start.assert_called_once_with(trade_date="2026-09-18")
        self.assertEqual(result["skipped"], "backfill_already_running")
        self.assertIsNone(collector._last_backfilled_date)

    def test_triggers_backfill_waits_for_completion_and_marks_date_done(self):
        after_settle = datetime(2026, 9, 18, 14, 0, tzinfo=TW_TZ)  # Friday
        success_result = {
            "tradeDate": "2026-09-18", "codeCount": 2, "codesProcessed": 2,
            "barsReplayed": 10, "signalsEmitted": 1, "failures": [],
        }
        statuses = iter([
            {"running": True, "result": None},
            {"running": True, "result": None},
            {"running": False, "result": success_result},
            {"running": False, "result": success_result},  # collect_once在迴圈結束後還會再讀一次最終狀態
        ])
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = after_settle
            with (
                patch.object(collector, "start_kline_signal_backfill_today", return_value={"started": True}) as mock_start,
                patch.object(collector, "kline_signal_backfill_status", side_effect=lambda: next(statuses)),
                patch.object(collector.time, "sleep"),
            ):
                result = collector.collect_once()
        mock_start.assert_called_once_with(trade_date="2026-09-18")
        self.assertEqual(result["tradeDate"], "2026-09-18")
        self.assertEqual(result["result"], success_result)
        self.assertEqual(collector._last_backfilled_date, "2026-09-18")

    def test_does_not_mark_date_done_when_backfill_result_has_error(self):
        # 回補整體失敗(result裡有error)時不該標記今天已經補完，讓下一輪
        # (POLL_SECONDS後)再重試，而不是整天就此放棄。
        after_settle = datetime(2026, 9, 18, 14, 0, tzinfo=TW_TZ)  # Friday
        error_result = {"error": "RuntimeError: broker unavailable"}
        statuses = iter([
            {"running": True, "result": None},
            {"running": False, "result": error_result},
            {"running": False, "result": error_result},  # collect_once在迴圈結束後還會再讀一次最終狀態
        ])
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = after_settle
            with (
                patch.object(collector, "start_kline_signal_backfill_today", return_value={"started": True}),
                patch.object(collector, "kline_signal_backfill_status", side_effect=lambda: next(statuses)),
                patch.object(collector.time, "sleep"),
            ):
                result = collector.collect_once()
        self.assertEqual(result["result"], error_result)
        self.assertIsNone(collector._last_backfilled_date)

    def test_new_trade_date_clears_previous_days_completion_flag(self):
        collector._last_backfilled_date = "2026-09-17"
        after_settle = datetime(2026, 9, 18, 14, 0, tzinfo=TW_TZ)  # Friday, 隔一個交易日
        success_result = {
            "tradeDate": "2026-09-18", "codeCount": 1, "codesProcessed": 1,
            "barsReplayed": 1, "signalsEmitted": 0, "failures": [],
        }
        statuses = iter([
            {"running": False, "result": success_result},
            {"running": False, "result": success_result},  # collect_once在迴圈結束後還會再讀一次最終狀態
        ])
        with patch.object(collector, "datetime") as mock_dt:
            mock_dt.now.return_value = after_settle
            with (
                patch.object(collector, "start_kline_signal_backfill_today", return_value={"started": True}) as mock_start,
                patch.object(collector, "kline_signal_backfill_status", side_effect=lambda: next(statuses)),
                patch.object(collector.time, "sleep"),
            ):
                result = collector.collect_once()
        mock_start.assert_called_once_with(trade_date="2026-09-18")
        self.assertNotEqual(result.get("skipped"), "already_backfilled_today")
        self.assertEqual(collector._last_backfilled_date, "2026-09-18")


if __name__ == "__main__":
    unittest.main()
