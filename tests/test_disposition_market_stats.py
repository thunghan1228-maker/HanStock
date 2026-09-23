"""disposition_market_stats.py：從bars_1d算橫斷面統計，驗證「起迄兩個營業日」是頭尾
兩天的收盤價（不是區間平均）、「含當日」的60日均量真的含當日、歷史天數不夠時是None
不是0、今天沒資料的股票不列入橫斷面平均。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database
from database import get_connection, initialize_database
from disposition_market_stats import (
    MIN_INDUSTRY_PEERS,
    build_market_snapshot,
    compute_stock_metrics,
    load_market_series,
)


def _business_days_ending(end: date, count: int) -> list[date]:
    """從end往前數count個「營業日」(週一到週五)，由舊到新排序，end本身算最後一天。"""
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _insert_bars(
    connection, code: str, dates: list[date], closes: list[float],
    volumes: list[float] | None = None, opens: list[float] | None = None,
) -> None:
    assert len(closes) == len(dates), f"closes({len(closes)}) 跟 dates({len(dates)}) 長度要一樣"
    volumes = volumes or [1000.0] * len(dates)
    opens = opens or closes
    assert len(volumes) == len(dates) and len(opens) == len(dates)
    rows = [
        (code, d.isoformat() + "T00:00:00+00:00", opens[idx], closes[idx] + 1, closes[idx] - 1, closes[idx], volumes[idx])
        for idx, d in enumerate(dates)
    ]
    connection.executemany(
        """
        INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_code, bar_time) DO UPDATE SET close = excluded.close, volume = excluded.volume
        """,
        rows,
    )


class DispositionMarketStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.today = date(2026, 9, 23)  # 週三
        self.days_95 = _business_days_ending(self.today, 95)  # 多留一點餘裕給90日窗口

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_change_6d_pct_is_first_vs_last_close_not_average(self):
        dates = self.days_95[-6:]
        closes = [100.0, 100.0, 100.0, 100.0, 100.0, 150.0]  # 頭100→尾150，6日累積=+50%
        with get_connection() as connection:
            _insert_bars(connection, "1101", dates, closes)
        snapshot = build_market_snapshot(self.today.isoformat())
        metrics = snapshot.metrics_by_code["1101"]
        self.assertAlmostEqual(metrics.change_6d_pct, 50.0)
        self.assertAlmostEqual(metrics.price_diff_6d, 50.0)

    def test_change_2d_30d_uses_start_and_end_close_only(self):
        """30營業日窗口中間怎麼震盪不影響結果，只看頭尾兩天。"""
        dates = self.days_95[-30:]
        middle = [5.0, 500.0, 1.0, 200.0] * 7  # 28筆亂震盪
        closes_30 = [80.0] + middle + [160.0]  # 頭=80，尾=160 → 100%；共1+28+1=30筆
        self.assertEqual(len(closes_30), 30)
        with get_connection() as connection:
            _insert_bars(connection, "1102", dates, closes_30)
        snapshot = build_market_snapshot(self.today.isoformat())
        metrics = snapshot.metrics_by_code["1102"]
        self.assertAlmostEqual(metrics.change_2d_30d_pct, 100.0)

    def test_missing_history_gives_none_not_zero(self):
        """只有10天資料：算得出6日漲跌，但30/60/90日窗口都應該是None，不是0或錯誤值。"""
        dates = self.days_95[-10:]
        closes = [100.0] * 9 + [110.0]
        with get_connection() as connection:
            _insert_bars(connection, "1103", dates, closes)
        snapshot = build_market_snapshot(self.today.isoformat())
        metrics = snapshot.metrics_by_code["1103"]
        self.assertIsNotNone(metrics.change_6d_pct)
        self.assertIsNone(metrics.change_2d_30d_pct)
        self.assertIsNone(metrics.change_2d_60d_pct)
        self.assertIsNone(metrics.change_2d_90d_pct)
        self.assertIsNone(metrics.volume_ratio_60d)

    def test_volume_ratio_60d_average_includes_today(self):
        """官方原文「最近六十個營業日(含當日)之日平均成交量」：60日均量本身含當日，
        不是拿今天跟前59天的均量比。"""
        dates = self.days_95[-60:]
        volumes = [100.0] * 59 + [1000.0]  # 60筆，最後一筆(今天)=1000，前59筆=100
        closes = [50.0] * 60
        with get_connection() as connection:
            _insert_bars(connection, "1104", dates, closes, volumes=volumes)
        snapshot = build_market_snapshot(self.today.isoformat())
        metrics = snapshot.metrics_by_code["1104"]
        expected_avg60 = (100.0 * 59 + 1000.0) / 60
        self.assertAlmostEqual(metrics.volume_ratio_60d, 1000.0 / expected_avg60)

    def test_close_above_open_ref_uses_todays_open(self):
        dates = self.days_95[-1:]
        with get_connection() as connection:
            _insert_bars(connection, "1105", dates, closes=[105.0], opens=[100.0])
            _insert_bars(connection, "1106", dates, closes=[95.0], opens=[100.0])
        snapshot = build_market_snapshot(self.today.isoformat())
        self.assertTrue(snapshot.metrics_by_code["1105"].close_above_open_ref)
        self.assertFalse(snapshot.metrics_by_code["1106"].close_above_open_ref)

    def test_is_6d_high_or_low_true_when_today_is_the_6d_max(self):
        dates = self.days_95[-6:]
        closes = [100.0, 105.0, 95.0, 102.0, 98.0, 110.0]  # 今天110是這6天最高
        with get_connection() as connection:
            _insert_bars(connection, "1109", dates, closes)
        snapshot = build_market_snapshot(self.today.isoformat())
        self.assertTrue(snapshot.metrics_by_code["1109"].is_6d_high_or_low)

    def test_is_6d_high_or_low_true_when_today_is_the_6d_min(self):
        dates = self.days_95[-6:]
        closes = [100.0, 105.0, 95.0, 102.0, 98.0, 90.0]  # 今天90是這6天最低
        with get_connection() as connection:
            _insert_bars(connection, "1110", dates, closes)
        snapshot = build_market_snapshot(self.today.isoformat())
        self.assertTrue(snapshot.metrics_by_code["1110"].is_6d_high_or_low)

    def test_is_6d_high_or_low_false_when_today_is_in_the_middle(self):
        """6天內先大漲又回落，今天收盤價落在6天區間中段——不是最高也不是最低。"""
        dates = self.days_95[-6:]
        closes = [100.0, 130.0, 95.0, 102.0, 98.0, 105.0]  # 最高130、最低95，今天105在中間
        with get_connection() as connection:
            _insert_bars(connection, "1111", dates, closes)
        snapshot = build_market_snapshot(self.today.isoformat())
        self.assertFalse(snapshot.metrics_by_code["1111"].is_6d_high_or_low)

    def test_is_6d_high_or_low_none_without_enough_history(self):
        dates = self.days_95[-3:]
        with get_connection() as connection:
            _insert_bars(connection, "1112", dates, closes=[100.0, 101.0, 102.0])
        snapshot = build_market_snapshot(self.today.isoformat())
        self.assertIsNone(snapshot.metrics_by_code["1112"].is_6d_high_or_low)

    def test_stock_without_todays_row_is_excluded_from_snapshot(self):
        """今天沒資料(停牌/還沒寫進bars_1d)：這檔不該出現在橫斷面裡，也不該拉低全體平均。"""
        missing_today = self.days_95[:-1]
        with get_connection() as connection:
            _insert_bars(connection, "1107", missing_today, [100.0] * len(missing_today))
            _insert_bars(connection, "1108", self.days_95, [100.0] * len(self.days_95))
        snapshot = build_market_snapshot(self.today.isoformat())
        self.assertNotIn("1107", snapshot.metrics_by_code)
        self.assertIn("1108", snapshot.metrics_by_code)

    def test_peer_average_is_cross_sectional_mean_of_available_values(self):
        with get_connection() as connection:
            for code, pct in (("2001", 10.0), ("2002", 20.0), ("2003", 30.0)):
                closes = [100.0] * (len(self.days_95) - 1) + [100.0 * (1 + pct / 100)]
                _insert_bars(connection, code, self.days_95, closes)
        snapshot = build_market_snapshot(self.today.isoformat())
        self.assertAlmostEqual(snapshot.peer_avg["change_6d_pct"], 20.0)

    def test_industry_average_requires_minimum_peer_count(self):
        industry_by_code = {f"300{i}": "半導體" for i in range(4)}  # 只有4檔，低於門檻5
        with get_connection() as connection:
            for i in range(4):
                code = f"300{i}"
                closes = [100.0] * (len(self.days_95) - 1) + [110.0]
                _insert_bars(connection, code, self.days_95, closes)
        snapshot = build_market_snapshot(self.today.isoformat(), industry_by_code=industry_by_code)
        self.assertNotIn("半導體", snapshot.industry_avg)

    def test_industry_average_computed_when_enough_peers(self):
        industry_by_code = {f"400{i}": "電子" for i in range(MIN_INDUSTRY_PEERS)}
        with get_connection() as connection:
            for i in range(MIN_INDUSTRY_PEERS):
                code = f"400{i}"
                closes = [100.0] * (len(self.days_95) - 1) + [100.0 + i]  # 漲跌%各不同
                _insert_bars(connection, code, self.days_95, closes)
        snapshot = build_market_snapshot(self.today.isoformat(), industry_by_code=industry_by_code)
        self.assertIn("電子", snapshot.industry_avg)
        self.assertIn("change_6d_pct", snapshot.industry_avg["電子"])

    def test_load_market_series_scopes_to_lookback_window(self):
        with get_connection() as connection:
            _insert_bars(connection, "9999", self.days_95, [100.0] * len(self.days_95))
        series = load_market_series(self.today.isoformat(), lookback_days=10)
        self.assertLessEqual(len(series["9999"].dates), 10)

    def test_compute_stock_metrics_returns_none_for_empty_series(self):
        from disposition_market_stats import StockDailySeries

        self.assertIsNone(compute_stock_metrics(StockDailySeries(code="0000")))


if __name__ == "__main__":
    unittest.main()
