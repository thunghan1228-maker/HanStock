"""disposition_phase3_assembly.py：第十二/十三款"從前一營業日起"的日期偏移(這是最
容易算錯的地方，跟Phase 2第七款券資比同一種邏輯)、6日累積比例用"分子分母各自加總6天
再相除"而不是"逐日比率平均"、60日均量門檻不足時回傳None(不會把還沒收集滿60天的資料
當真)、6日窗口裡缺任一天的總成交量就讓那個比例算不出來(不補0假裝有資料)。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from database import get_connection, initialize_database
from disposition_phase3_assembly import (
    _avg_60d_from_previous_day,
    _prev_day_and_cum_ratio,
    build_phase3_by_code,
)
from disposition_phase3_store import save_day_trading_rows, save_sbl_short_sale_rows


def _row(trade_date: str, volume: float) -> dict:
    return {"trade_date": trade_date, "day_trading_volume": volume}


class PrevDayAndCumRatioTests(unittest.TestCase):
    def test_uses_previous_day_when_history_includes_today(self):
        """關鍵行為：rows最後一筆是「今天」，要跳過才拿得到「前一營業日」——這裡驗證真的
        跳過了最後一筆(用一個明顯錯誤的哨兵值放在「今天」那筆)。"""
        rows = [
            _row("2026-09-16", 10.0), _row("2026-09-17", 10.0), _row("2026-09-18", 10.0),
            _row("2026-09-19", 10.0), _row("2026-09-20", 10.0), _row("2026-09-22", 20.0),
            _row("2026-09-23", 999999.0),  # 今天，不該被用到
        ]
        total_volume_by_date = {
            "2026-09-16": 100.0, "2026-09-17": 100.0, "2026-09-18": 100.0,
            "2026-09-19": 100.0, "2026-09-20": 100.0, "2026-09-22": 100.0,
        }
        prev_volume, prev_ratio, cum_ratio = _prev_day_and_cum_ratio(
            rows, "day_trading_volume", "2026-09-23", total_volume_by_date,
        )
        self.assertEqual(prev_volume, 20.0)  # 前一營業日(9/22)，不是今天的999999
        self.assertAlmostEqual(prev_ratio, 20.0 / 100.0 * 100)
        # 6日窗口=9/16~9/20+9/22，分子=10*5+20=70，分母=100*6=600
        self.assertAlmostEqual(cum_ratio, 70.0 / 600.0 * 100)

    def test_includes_today_false_uses_last_row_directly(self):
        """history本身只到前一營業日為止(還沒收集到今天)，不用再跳過。"""
        rows = [_row("2026-09-22", 20.0)]
        total_volume_by_date = {"2026-09-22": 100.0}
        prev_volume, prev_ratio, _cum = _prev_day_and_cum_ratio(
            rows, "day_trading_volume", "2026-09-23", total_volume_by_date,
        )
        self.assertEqual(prev_volume, 20.0)
        self.assertAlmostEqual(prev_ratio, 20.0)

    def test_fewer_than_6_days_gives_none_cum_ratio_but_keeps_prev_day(self):
        rows = [_row("2026-09-21", 10.0), _row("2026-09-22", 20.0)]
        total_volume_by_date = {"2026-09-21": 100.0, "2026-09-22": 100.0}
        prev_volume, prev_ratio, cum_ratio = _prev_day_and_cum_ratio(
            rows, "day_trading_volume", "2026-09-23", total_volume_by_date,
        )
        self.assertEqual(prev_volume, 20.0)
        self.assertIsNotNone(prev_ratio)
        self.assertIsNone(cum_ratio)

    def test_gap_in_total_volume_within_window_gives_none_cum_ratio(self):
        """6天window裡有一天bars_1d沒有總成交量(缺資料)，不補0，整個累積比例回傳None。"""
        rows = [_row(f"2026-09-{d:02d}", 10.0) for d in range(16, 23)]  # 9/16~9/22，7筆
        total_volume_by_date = {f"2026-09-{d:02d}": 100.0 for d in range(16, 23) if d != 18}  # 缺9/18
        _prev_volume, _prev_ratio, cum_ratio = _prev_day_and_cum_ratio(
            rows, "day_trading_volume", "2026-09-23", total_volume_by_date,
        )
        self.assertIsNone(cum_ratio)

    def test_no_history_returns_all_none(self):
        result = _prev_day_and_cum_ratio([], "day_trading_volume", "2026-09-23", {})
        self.assertEqual(result, (None, None, None))

    def test_zero_total_volume_that_day_gives_none_prev_ratio(self):
        rows = [_row("2026-09-22", 20.0)]
        total_volume_by_date = {"2026-09-22": 0.0}
        _prev_volume, prev_ratio, _cum = _prev_day_and_cum_ratio(
            rows, "day_trading_volume", "2026-09-23", total_volume_by_date,
        )
        self.assertIsNone(prev_ratio)


class Avg60dFromPreviousDayTests(unittest.TestCase):
    def test_fewer_than_60_days_returns_none(self):
        rows = [_row(f"d{i:02d}", 10.0) for i in range(59)] + [_row("today", 999999.0)]
        result = _avg_60d_from_previous_day(rows, "day_trading_volume", "today")
        self.assertIsNone(result)

    def test_exactly_60_days_computes_average_excluding_today(self):
        rows = [_row(f"d{i:02d}", 10.0) for i in range(60)] + [_row("today", 999999.0)]
        result = _avg_60d_from_previous_day(rows, "day_trading_volume", "today")
        self.assertAlmostEqual(result, 10.0)  # 今天的999999哨兵值沒被用到

    def test_no_history_returns_none(self):
        self.assertIsNone(_avg_60d_from_previous_day([], "day_trading_volume", "today"))


class BuildPhase3ByCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_bars(self, code: str, volume: float, days: range) -> None:
        with get_connection() as connection:
            rows = [
                (code, f"2026-09-{d:02d}T00:00:00+00:00", 10.0, 10.0, 10.0, 10.0, volume)
                for d in days
            ]
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(stock_code, bar_time) DO NOTHING",
                rows,
            )

    def test_end_to_end_day_trading_and_sbl(self):
        self._seed_bars("2330", 100.0, range(16, 23))
        for d in range(16, 23):
            save_day_trading_rows([{"code": "2330", "tradeDate": f"2026-09-{d:02d}", "volume": 10.0}])
            save_sbl_short_sale_rows([{"code": "2330", "tradeDate": f"2026-09-{d:02d}", "volume": 5.0}])
        result = build_phase3_by_code("2026-09-22", {"2330"})
        self.assertAlmostEqual(result["2330"]["day_trading_prev_day_ratio_pct"], 10.0)
        self.assertAlmostEqual(result["2330"]["sbl_short_sale_cum_6d_ratio_pct"], 5.0)
        self.assertIsNone(result["2330"]["sbl_short_sale_avg_60d_volume"])  # 只有6天歷史，不足60天

    def test_missing_stock_has_all_none_but_still_present(self):
        result = build_phase3_by_code("2026-09-23", {"9999"})
        self.assertIn("9999", result)
        self.assertIsNone(result["9999"]["day_trading_prev_day_ratio_pct"])
        self.assertIsNone(result["9999"]["sbl_short_sale_cum_6d_ratio_pct"])


if __name__ == "__main__":
    unittest.main()
