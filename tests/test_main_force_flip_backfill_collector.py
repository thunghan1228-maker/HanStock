from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
import main_force_flip_backfill_collector as collector

TW = timezone(timedelta(hours=8))


def at(day: str, hour: int, minute: int) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=TW)


class FlipBackfillCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        self.runs: list[str] = []

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _ok(self, trade_date: str) -> dict:
        self.runs.append(trade_date)
        return {"tradeDate": trade_date, "processed": 3, "signalsEmitted": 1, "quotaBlocked": False}

    def test_after_close_replays_today_once(self) -> None:
        # 2026-09-22 是週二。
        result = collector.collect_once(now=at("2026-09-22", 13, 45), run_backfill=self._ok)

        self.assertEqual(result["tradeDate"], "2026-09-22")
        self.assertTrue(result["done"])
        self.assertTrue(collector.backfill_done("2026-09-22"))
        again = collector.collect_once(now=at("2026-09-22", 14, 0), run_backfill=self._ok)
        self.assertEqual(again, {"skipped": "nothing_to_do"})
        self.assertEqual(self.runs, ["2026-09-22"])

    def test_quota_blocked_day_is_retried_next_morning_before_open(self) -> None:
        # 使用者實際情境：偵測器 13:13 才上線、當天額度早被燒光，收盤後補不成；
        # 隔天開盤前額度恢復，要把前一個交易日補回來。
        blocked = collector.collect_once(
            now=at("2026-09-22", 13, 45),
            run_backfill=lambda d: {"tradeDate": d, "processed": 0, "quotaBlocked": True},
        )
        self.assertFalse(blocked["done"])
        self.assertFalse(collector.backfill_done("2026-09-22"))

        during_session = collector.collect_once(now=at("2026-09-23", 10, 0), run_backfill=self._ok)
        self.assertEqual(during_session, {"skipped": "nothing_to_do"})  # 盤中不重播，避免干擾即時偵測

        morning = collector.collect_once(now=at("2026-09-23", 7, 30), run_backfill=self._ok)
        self.assertEqual(morning["tradeDate"], "2026-09-22")
        self.assertTrue(morning["done"])
        self.assertEqual(self.runs, ["2026-09-22"])

    def test_before_close_nothing_and_weekend_morning_targets_friday(self) -> None:
        self.assertEqual(collector.collect_once(now=at("2026-09-22", 13, 30), run_backfill=self._ok), {"skipped": "nothing_to_do"})
        saturday = collector.collect_once(now=at("2026-09-26", 6, 0), run_backfill=self._ok)
        self.assertEqual(saturday["tradeDate"], "2026-09-25")
        self.assertEqual(collector.collect_once(now=at("2026-09-26", 14, 0), run_backfill=self._ok), {"skipped": "nothing_to_do"})

    def test_backfill_error_keeps_date_pending(self) -> None:
        failed = collector.collect_once(
            now=at("2026-09-22", 13, 45), run_backfill=lambda d: {"tradeDate": d, "error": "boom"},
        )
        self.assertFalse(failed["done"])
        self.assertFalse(collector.backfill_done("2026-09-22"))


if __name__ == "__main__":
    unittest.main()
