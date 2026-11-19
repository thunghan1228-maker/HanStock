import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from database import initialize_database, save_bars
from datetime import date, datetime, timedelta, timezone

import daily_bars_collector as collector

UTC = timezone.utc


class DailyBarsCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_first_run_does_a_full_backfill(self):
        with patch.object(collector, "download_official_daily_bars", return_value={"inserted_bars": 0}) as mock_download:
            result = collector.collect_once()
        mock_download.assert_called_once_with(days=collector.BACKFILL_DAYS, run_triangle_scan=False)
        self.assertEqual(result["mode"], "backfill")

    def test_subsequent_runs_only_catch_up_recent_days_once_backfill_is_actually_complete(self):
        # 最早的交易日要老到足以證明全量回補真的補到接近BACKFILL_DAYS，
        # 不能只因為「有任何一筆資料」就當作已經補完。
        old_enough = date.today() - timedelta(days=collector.BACKFILL_DAYS - 1)
        save_bars("bars_1d", "2330", [{
            "time": datetime(old_enough.year, old_enough.month, old_enough.day, tzinfo=UTC),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000,
        }])
        with patch.object(collector, "download_official_daily_bars", return_value={"inserted_bars": 0}) as mock_download:
            result = collector.collect_once()
        mock_download.assert_called_once_with(days=collector.CATCHUP_DAYS, run_triangle_scan=False)
        self.assertEqual(result["mode"], "catchup")

    def test_interrupted_backfill_resumes_as_full_backfill_not_stuck_on_catchup(self):
        # 重現實際發生過的bug：全量回補跑到一半就被中斷(例如Railway重新
        # 部署把背景執行緒砍掉)，只補進最近幾天的資料；barCount已經不是0，
        # 但最早的交易日離「應該回補到」還差得遠。下一輪不能被barCount>0
        # 誤導成只做5天catchup，永遠補不回缺口，要偵測到範圍不夠、重跑
        # 全量回補(反正_save_day是ON CONFLICT DO NOTHING，重跑不重複)。
        recent_only = date.today() - timedelta(days=3)
        save_bars("bars_1d", "2330", [{
            "time": datetime(recent_only.year, recent_only.month, recent_only.day, tzinfo=UTC),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000,
        }])
        with patch.object(collector, "download_official_daily_bars", return_value={"inserted_bars": 0}) as mock_download:
            result = collector.collect_once()
        mock_download.assert_called_once_with(days=collector.BACKFILL_DAYS, run_triangle_scan=False)
        self.assertEqual(result["mode"], "backfill")

    def test_collect_once_prunes_after_downloading(self):
        for day in range(1, 40):
            save_bars("bars_1d", "2330", [{
                "time": datetime(2026, 8 if day <= 31 else 9, day if day <= 31 else day - 31, tzinfo=UTC),
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000,
            }])
        with patch.object(collector, "download_official_daily_bars", return_value={"inserted_bars": 0}):
            with patch.object(collector, "KEEP_DAYS", 5):
                result = collector.collect_once()
        self.assertGreater(result["pruned"], 0)


class NeedsFullBackfillTests(unittest.TestCase):
    def test_empty_storage_needs_backfill(self):
        self.assertTrue(collector._needs_full_backfill({"barCount": 0, "firstTradeDate": None}))

    def test_missing_first_trade_date_needs_backfill(self):
        self.assertTrue(collector._needs_full_backfill({"barCount": 5, "firstTradeDate": None}))

    def test_coverage_reaching_close_to_backfill_days_does_not_need_backfill(self):
        today = date(2026, 9, 21)
        earliest = today - timedelta(days=collector.BACKFILL_DAYS - 1)
        status = {"barCount": 100, "firstTradeDate": earliest.isoformat()}
        self.assertFalse(collector._needs_full_backfill(status, today=today))

    def test_coverage_from_an_interrupted_run_still_needs_backfill(self):
        today = date(2026, 9, 21)
        earliest = today - timedelta(days=5)
        status = {"barCount": 100, "firstTradeDate": earliest.isoformat()}
        self.assertTrue(collector._needs_full_backfill(status, today=today))

    def test_seven_day_slack_tolerates_holiday_gaps_near_the_threshold(self):
        today = date(2026, 9, 21)
        # 剛好落在門檻7天餘裕內，不該被誤判成需要重跑全量。
        earliest = today - timedelta(days=collector.BACKFILL_DAYS - 5)
        status = {"barCount": 100, "firstTradeDate": earliest.isoformat()}
        self.assertFalse(collector._needs_full_backfill(status, today=today))


if __name__ == "__main__":
    unittest.main()


class NextSleepTests(unittest.TestCase):
    """2026-11-19 使用者：強勢紅K／創高黑龍收盤後一直停在前一天——收盤後等官方日K時改成每 10 分鐘抓。"""
    TW = timezone(timedelta(hours=8))

    def at(self, hh, mm, day=19):
        return datetime(2026, 11, day, hh, mm, tzinfo=self.TW)   # 11/19 週四、11/21 週六

    def test_after_close_until_today_complete_polls_fast(self):
        with patch.object(collector, "_is_trading_day", return_value=True):
            self.assertEqual(collector.next_sleep_seconds(self.at(13, 45), counts=(0, 1900)), collector.FAST_POLL_SECONDS)
            # 上市先進來、上櫃還沒：還是快
            self.assertEqual(collector.next_sleep_seconds(self.at(14, 35), counts=(1050, 1900)), collector.FAST_POLL_SECONDS)
            # 上市上櫃都到了：回到原本的間隔
            self.assertEqual(collector.next_sleep_seconds(self.at(15, 5), counts=(1890, 1900)), collector.POLL_SECONDS)
            # 16:30 以後照舊
            self.assertEqual(collector.next_sleep_seconds(self.at(17, 0), counts=(0, 1900)), collector.POLL_SECONDS)

    def test_before_window_wakes_up_at_1335(self):
        with patch.object(collector, "_is_trading_day", return_value=True):
            self.assertEqual(collector.next_sleep_seconds(self.at(13, 5)), 30 * 60 + 1)
            self.assertEqual(collector.next_sleep_seconds(self.at(9, 0)), collector.POLL_SECONDS)

    def test_holiday_keeps_normal_interval(self):
        self.assertEqual(collector.next_sleep_seconds(self.at(14, 0, day=21), counts=(0, 1900)), collector.POLL_SECONDS)   # 週六

    def test_day_counts_reads_today_and_previous_trading_day(self):
        for d, n in (("2026-11-18", 3), ("2026-11-19", 1)):
            for i in range(n):
                save_bars("bars_1d", str(2330 + i), [{"time": datetime.fromisoformat(d + "T00:00:00+08:00"),
                                                      "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}])
        self.assertEqual(collector._day_counts("2026-11-19"), (1, 3))   # bar_time 存成台北時間（+08:00），前 10 碼就是交易日
