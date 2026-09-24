"""族群個股日K歷史回補（FinMind 單日全市場），讓 MA240／均線分數算得出來。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import brew_launch
import daily_bars_history_backfill as module
import database
from database import get_connection
from official_daily_bars import _save_day

UTC = timezone.utc
FAKE_GROUPS = {"半導體": [("2330", "台積電"), ("8069", "元太")], "股期標的": [("2330", "台積電")]}


def _bar(code, name, market, day, close):
    return {"stock_code": code, "stock_name": name, "market": market, "time": datetime.fromisoformat(day).replace(tzinfo=UTC),
            "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000}


def _finmind_row(code, close, volume_shares=2_000_000):
    return {"stock_id": code, "open": close, "max": close + 2, "min": close - 2, "close": close, "Trading_Volume": volume_shares}


class GroupHistoryBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.groups_patch = patch.object(module, "STOCK_GROUPS", FAKE_GROUPS)
        self.groups_patch.start()
        self.env_patch = patch.dict(os.environ, {"FINMIND_TOKEN": "test-token"})
        self.env_patch.start()
        # 兩檔都已經在 stocks 表（官方收集器建的）；09/22 已經完整，09/23 只有 2330（上櫃的 8069 缺）
        _save_day([_bar("2330", "台積電", "TWSE", "2026-09-22", 1000), _bar("8069", "元太", "OTC", "2026-09-22", 200)])
        _save_day([_bar("2330", "台積電", "TWSE", "2026-09-23", 1010)])

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.groups_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _fetcher(self, calls, fail_on=None):
        def fetch(url, params):
            day = params["start_date"]
            calls.append(day)
            if fail_on and day == fail_on:
                raise OSError("boom")
            # 全市場：含不在 stocks 表的代號 9999（要略過）
            return {"data": [_finmind_row("2330", 999.0), _finmind_row("8069", 210.0), _finmind_row("9999", 5.0)]}
        return fetch

    def _bars(self, code):
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT substr(bar_time, 1, 10) AS d, close, volume FROM bars_1d WHERE stock_code = ? ORDER BY bar_time", (code,)
            ).fetchall()
        return {row["d"]: (row["close"], row["volume"]) for row in rows}

    def test_fills_only_days_below_coverage_and_known_codes(self) -> None:
        calls: list[str] = []
        with patch.object(module, "LOOKBACK_CALENDAR_DAYS", 3):  # 09/21(一)～09/23(三)
            result = module.backfill_group_history(today=date(2026, 9, 24), delay=0, fetcher=self._fetcher(calls))
        # 09/21 完全沒資料、09/23 只有一半（8069 缺）→ 要補；09/22 兩檔都有 → 跳過
        self.assertEqual(calls, ["2026-09-21", "2026-09-23"])
        self.assertEqual(result["requestedDays"], 2)
        self.assertEqual(result["insertedBars"], 3)                           # 09/21 兩檔＋09/23 的 8069
        self.assertEqual(result["failures"], [])
        self.assertEqual(self._bars("8069")["2026-09-23"], (210.0, 2000))   # 補上缺的那天，量換成張
        self.assertEqual(self._bars("2330")["2026-09-23"], (1010.0, 1000))  # 已經有的不覆蓋
        self.assertEqual(self._bars("9999"), {})                              # 不在 stocks 表的不寫
        with get_connection() as connection:
            markets = {row["stock_code"]: row["market"] for row in connection.execute("SELECT stock_code, market FROM stocks").fetchall()}
        self.assertEqual(markets, {"2330": "TWSE", "8069": "OTC"})           # 市場不會被改掉

    def test_failed_day_is_reported(self) -> None:
        calls: list[str] = []
        with patch.object(module, "LOOKBACK_CALENDAR_DAYS", 3):
            result = module.backfill_group_history(today=date(2026, 9, 24), delay=0, fetcher=self._fetcher(calls, fail_on="2026-09-21"))
        self.assertEqual([f["date"] for f in result["failures"]], ["2026-09-21"])
        self.assertEqual(self._bars("8069")["2026-09-23"], (210.0, 2000))   # 其他天照補

    def test_run_once_marks_done_only_without_failures(self) -> None:
        with patch.object(module, "backfill_group_history", lambda: {"insertedBars": 5, "failures": [{"date": "2026-09-21", "error": "x"}]}):
            module._run_once()
        self.assertFalse(module.backfill_state()["done"])      # 有失敗 → 下次開機重試
        with patch.object(module, "backfill_group_history", lambda: {"insertedBars": 7, "failures": []}):
            module._run_once()
        state = module.backfill_state()
        self.assertTrue(state["done"])
        self.assertEqual(state["result"]["insertedBars"], 7)
        with patch.object(module, "backfill_group_history", lambda: self.fail("done 之後不該再跑")):
            module._run_once()

    def test_brew_launch_payload_carries_backfill_status_and_cache_clears(self) -> None:
        with patch.object(brew_launch, "compute_brew_launch", lambda session=None: {"status": "ok", "stocks": {}}):
            brew_launch.clear_cache()
            payload = brew_launch.get_brew_launch()
            self.assertIn("historyBackfill", payload)
            self.assertFalse(payload["historyBackfill"]["done"])
            self.assertIsNotNone(brew_launch._cache["value"])
            brew_launch.clear_cache()
            self.assertIsNone(brew_launch._cache["value"])


if __name__ == "__main__":
    unittest.main()
