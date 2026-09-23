import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import database
from database import get_connection, initialize_database
from otc_index import OTC_INDEX_HUB_CODE, TW_TZ
from otc_index_store import save_index_bars_5m
from stock_bars_5m_store import (
    bars_5m_coverage_complete,
    load_stock_bars_5m_before,
    load_stock_bars_5m_on,
    prune_stock_bars_5m,
    save_stock_bars_5m,
    save_stock_bars_5m_many,
)


def _ts(day: int, hour: int, minute: int, month: int = 9) -> int:
    return int(datetime(2026, month, day, hour, minute, tzinfo=TW_TZ).timestamp() * 1000)


def _bar(day: int, hour: int, minute: int, close: float = 100.0, **extra) -> dict:
    return {"ts": _ts(day, hour, minute), "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 12, **extra}


class StockBars5mStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _count(self, code: str) -> int:
        with get_connection() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM bars_5m WHERE stock_code = ?", (code,)
            ).fetchone()[0]

    def test_save_then_load_before_trade_date_returns_last_bars_oldest_first(self):
        saved = save_stock_bars_5m("2330", [
            _bar(16, 13, 20, 98.0), _bar(17, 9, 0, 99.0), _bar(17, 9, 5, 100.0), _bar(17, 13, 25, 101.0),
            _bar(18, 9, 0, 200.0),  # 當天的 K 不能當昨天種子
        ])
        self.assertEqual(saved, 5)
        bars = load_stock_bars_5m_before("2330", "2026-09-18", limit=3)
        self.assertEqual([b["close"] for b in bars], [99.0, 100.0, 101.0])
        self.assertEqual(bars[0]["ts"], _ts(17, 9, 0))
        self.assertEqual(bars[-1]["volume"], 12)

    def test_save_upserts_same_bar_time(self):
        save_stock_bars_5m("2330", [_bar(17, 9, 0, 99.0)])
        save_stock_bars_5m("2330", [_bar(17, 9, 0, 99.5)])
        self.assertEqual(self._count("2330"), 1)
        self.assertEqual(load_stock_bars_5m_before("2330", "2026-09-18", 5)[0]["close"], 99.5)

    def test_save_skips_out_of_session_or_invalid_bars_and_normalizes_code(self):
        saved = save_stock_bars_5m(" 2330 ", [
            _bar(17, 8, 55, 99.0),   # 盤前
            _bar(17, 13, 30, 99.0),  # 13:30 之後
            _bar(17, 9, 0, 0.0),     # 價格 0
            {"open": 1},             # 缺欄位
            _bar(17, 9, 5, 99.0),
        ])
        self.assertEqual(saved, 1)
        self.assertEqual(self._count("2330"), 1)

    def test_save_many_writes_multiple_codes_in_one_call_and_skips_otc_index(self):
        saved = save_stock_bars_5m_many({
            "2330": [_bar(17, 9, 0, 99.0), _bar(17, 9, 5, 99.5)],
            "2317": [_bar(17, 9, 0, 50.0)],
            OTC_INDEX_HUB_CODE: [_bar(17, 9, 0, 250.0)],
            "": [_bar(17, 9, 0, 1.0)],
        })
        self.assertEqual(saved, 3)
        self.assertEqual(self._count("2330"), 2)
        self.assertEqual(self._count("2317"), 1)
        self.assertEqual(self._count(OTC_INDEX_HUB_CODE), 0)
        self.assertEqual(save_stock_bars_5m_many({}), 0)

    def test_load_ignores_other_codes_and_respects_limit(self):
        save_stock_bars_5m("2317", [_bar(17, 9, 0, 50.0)])
        save_stock_bars_5m("2330", [_bar(17, 9, 0, 99.0), _bar(17, 9, 5, 99.5), _bar(17, 9, 10, 100.0)])
        bars = load_stock_bars_5m_before("2330", "2026-09-18", limit=2)
        self.assertEqual([b["close"] for b in bars], [99.5, 100.0])
        self.assertEqual(load_stock_bars_5m_before("2330", "2026-09-18", limit=0), [])
        self.assertEqual(load_stock_bars_5m_before("9999", "2026-09-18", limit=5), [])

    def test_prune_removes_old_stock_bars_but_keeps_otc_index(self):
        save_stock_bars_5m("2330", [_bar(1, 9, 0, 99.0), _bar(17, 9, 0, 100.0)])
        save_index_bars_5m([_bar(1, 9, 0, 250.0)])
        removed = prune_stock_bars_5m(keep_calendar_days=15, today="2026-09-18")
        self.assertEqual(removed, 1)
        self.assertEqual(self._count("2330"), 1)
        self.assertEqual(self._count(OTC_INDEX_HUB_CODE), 1)
        self.assertEqual(load_stock_bars_5m_before("2330", "2026-09-18", 5)[0]["close"], 100.0)

    def _full_session_bars(self, day: int, close: float = 100.0):
        """09:00~13:25每5分鐘一根、共54根，模擬即時路徑整天連續追到的一天。"""
        bars = []
        minute = 0
        while minute <= 4 * 60 + 25:  # 09:00 起算的分鐘數，到 13:25 為止
            hour, mins = 9 + minute // 60, minute % 60
            bars.append(_bar(day, hour, mins, close))
            minute += 5
        return bars

    def test_coverage_complete_true_for_a_full_uninterrupted_session(self):
        save_stock_bars_5m("2330", self._full_session_bars(18))
        self.assertTrue(bars_5m_coverage_complete("2330", "2026-09-18"))

    def test_coverage_incomplete_when_no_bars_for_that_day(self):
        self.assertFalse(bars_5m_coverage_complete("2330", "2026-09-18"))

    def test_coverage_incomplete_when_a_middle_bar_is_missing(self):
        bars = self._full_session_bars(18)
        del bars[30]  # 拿掉中間一根，模擬晚訂閱恢復後中途斷過的缺口
        save_stock_bars_5m("2330", bars)
        self.assertFalse(bars_5m_coverage_complete("2330", "2026-09-18"))

    def test_coverage_incomplete_when_first_bar_is_not_the_open(self):
        bars = self._full_session_bars(18)[2:]  # 09:10 才開始追到，不是真正的09:00
        save_stock_bars_5m("2330", bars)
        self.assertFalse(bars_5m_coverage_complete("2330", "2026-09-18"))

    def test_coverage_incomplete_when_tracking_stopped_before_the_close(self):
        bars = self._full_session_bars(18)[:-5]  # 提前停在中途，沒追到收盤前最後一根
        save_stock_bars_5m("2330", bars)
        self.assertFalse(bars_5m_coverage_complete("2330", "2026-09-18"))

    def test_coverage_only_checks_the_requested_trade_date(self):
        save_stock_bars_5m("2330", self._full_session_bars(17))  # 完整的是前一天，不是要問的那天
        self.assertFalse(bars_5m_coverage_complete("2330", "2026-09-18"))


    def test_load_on_returns_only_that_days_bars_oldest_first(self):
        save_stock_bars_5m("2330", [_bar(17, 13, 25, 90.0), _bar(18, 9, 0, 100.0), _bar(18, 9, 5, 101.0), _bar(19, 9, 0, 110.0)])
        bars = load_stock_bars_5m_on("2330", "2026-09-18")
        self.assertEqual([b["close"] for b in bars], [100.0, 101.0])
        self.assertEqual(bars[0]["ts"], _ts(18, 9, 0))

    def test_load_on_returns_empty_when_nothing_stored_for_that_day(self):
        save_stock_bars_5m("2330", [_bar(17, 9, 0, 100.0)])
        self.assertEqual(load_stock_bars_5m_on("2330", "2026-09-18"), [])
        self.assertEqual(load_stock_bars_5m_on("9999", "2026-09-18"), [])


if __name__ == "__main__":
    unittest.main()
