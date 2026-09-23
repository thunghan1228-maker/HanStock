"""disposition_fundamentals_assembly.py：股本推算週轉率、"前一營業日"券資比/使用率的
日期偏移(這是最容易算錯的地方)、6日最低券資比排除今天、全體平均值只算有值的股票。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import database
from database import get_connection, initialize_database
from disposition_fundamentals_assembly import (
    build_fundamentals_by_code,
    compute_margin_based_fundamentals,
    compute_price_based_fundamentals,
)
from disposition_fundamentals_store import save_fundamentals_rows, save_margin_short_rows


def _bar(day: int, close: float, volume: float) -> dict:
    return {
        "ts": int(datetime(2026, 9, day).timestamp() * 1000),
        "open": close, "high": close, "low": close, "close": close, "volume": volume,
    }


class ComputePriceBasedFundamentalsTests(unittest.TestCase):
    def test_computes_turnover_from_market_value_and_close(self):
        # 市值1,000,000元、收盤價10元 → 發行股數100,000股；今天量50張=50,000股
        # → 週轉率50,000/100,000*100=50%
        bars = [_bar(d, 10.0, 100) for d in range(17, 22)] + [_bar(22, 10.0, 50)]
        result = compute_price_based_fundamentals(
            "2330", fundamentals_today={"peRatio": 15.0, "pbr": 2.0, "marketValue": 1_000_000.0},
            daily_bars=bars,
        )
        self.assertAlmostEqual(result.turnover_pct, 50.0)
        self.assertEqual(result.pe_ratio, 15.0)
        self.assertEqual(result.pbr, 2.0)

    def test_cum_turnover_6d_needs_exactly_6_bars(self):
        bars = [_bar(d, 10.0, 100) for d in range(17, 23)]  # 6筆
        result = compute_price_based_fundamentals(
            "2330", fundamentals_today={"marketValue": 1_000_000.0}, daily_bars=bars,
        )
        self.assertIsNotNone(result.cum_turnover_6d_pct)
        # 6天各100張=600張=600,000股 / 100,000股 *100 = 600%
        self.assertAlmostEqual(result.cum_turnover_6d_pct, 600.0)

    def test_fewer_than_6_bars_gives_none_cum_turnover(self):
        bars = [_bar(d, 10.0, 100) for d in range(20, 23)]  # 只有3筆
        result = compute_price_based_fundamentals(
            "2330", fundamentals_today={"marketValue": 1_000_000.0}, daily_bars=bars,
        )
        self.assertIsNone(result.cum_turnover_6d_pct)

    def test_pe_pbr_still_set_when_market_value_missing(self):
        bars = [_bar(22, 10.0, 50)]
        result = compute_price_based_fundamentals(
            "2330", fundamentals_today={"peRatio": 15.0, "pbr": 2.0, "marketValue": None},
            daily_bars=bars,
        )
        self.assertEqual(result.pe_ratio, 15.0)
        self.assertIsNone(result.turnover_pct)

    def test_no_fundamentals_or_bars_returns_all_none(self):
        result = compute_price_based_fundamentals("2330", fundamentals_today=None, daily_bars=[])
        self.assertIsNone(result.pe_ratio)
        self.assertIsNone(result.turnover_pct)


class ComputeMarginBasedFundamentalsTests(unittest.TestCase):
    def _row(self, trade_date: str, margin_balance: float, margin_limit: float, short_balance: float, short_limit: float) -> dict:
        return {
            "trade_date": trade_date, "margin_today_balance": margin_balance, "margin_limit": margin_limit,
            "short_today_balance": short_balance, "short_limit": short_limit,
        }

    def test_uses_previous_day_when_history_includes_today(self):
        """關鍵行為：history最後一筆是「今天」，第七款要用的是「前一營業日」，
        不是今天——這裡驗證真的跳過了最後一筆。"""
        history = [
            self._row("2026-09-21", margin_balance=1000, margin_limit=2000, short_balance=100, short_limit=500),
            self._row("2026-09-22", margin_balance=800, margin_limit=2000, short_balance=200, short_limit=500),
            self._row("2026-09-23", margin_balance=999999, margin_limit=999999, short_balance=999999, short_limit=999999),  # 今天，不該被用到
        ]
        ratio, margin_usage, short_usage, min_6d = compute_margin_based_fundamentals(history, includes_today=True)
        self.assertAlmostEqual(ratio, 200 / 800 * 100)  # 前一營業日(9/22)的券資比
        self.assertAlmostEqual(margin_usage, 800 / 2000 * 100)
        self.assertAlmostEqual(short_usage, 200 / 500 * 100)

    def test_min_6d_excludes_today_but_includes_all_prior_days(self):
        history = [
            self._row("2026-09-16", margin_balance=1000, margin_limit=2000, short_balance=50, short_limit=500),  # 5%
            self._row("2026-09-17", margin_balance=1000, margin_limit=2000, short_balance=300, short_limit=500),  # 30%
            self._row("2026-09-18", margin_balance=1000, margin_limit=2000, short_balance=999999, short_limit=999999),  # 今天，排除
        ]
        _ratio, _mu, _su, min_6d = compute_margin_based_fundamentals(history, includes_today=True)
        self.assertAlmostEqual(min_6d, 5.0)  # 最低是9/16的5%，不是被今天的極端值拉低

    def test_no_history_returns_all_none(self):
        result = compute_margin_based_fundamentals([], includes_today=True)
        self.assertEqual(result, (None, None, None, None))

    def test_includes_today_false_uses_last_row_directly(self):
        """今天還沒收集到融資融券資料時，history本身就只到前一營業日，不用再跳過。"""
        history = [self._row("2026-09-22", margin_balance=800, margin_limit=2000, short_balance=200, short_limit=500)]
        ratio, _mu, _su, _min6 = compute_margin_based_fundamentals(history, includes_today=False)
        self.assertAlmostEqual(ratio, 200 / 800 * 100)


class BuildFundamentalsByCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed_bars(self, code: str, close: float, volume: float, days: range) -> None:
        with get_connection() as connection:
            rows = [
                (code, f"2026-09-{d:02d}T00:00:00+00:00", close, close, close, close, volume)
                for d in days
            ]
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(stock_code, bar_time) DO NOTHING",
                rows,
            )

    def test_end_to_end_with_peer_average(self):
        self._seed_bars("2330", 100.0, 10.0, range(17, 23))
        self._seed_bars("2317", 50.0, 5.0, range(17, 23))
        save_fundamentals_rows([
            {"code": "2330", "tradeDate": "2026-09-22", "peRatio": 20.0, "pbr": 3.0, "marketValue": 100_000.0},
            {"code": "2317", "tradeDate": "2026-09-22", "peRatio": 10.0, "pbr": 1.0, "marketValue": 25_000.0},
        ])
        result = build_fundamentals_by_code("2026-09-22", {"2330", "2317"})
        self.assertAlmostEqual(result["2330"]["pe_ratio_peer_avg"], 15.0)  # (20+10)/2
        self.assertAlmostEqual(result["2330"]["pbr_peer_avg"], 2.0)  # (3+1)/2
        self.assertIsNotNone(result["2330"]["turnover_pct"])

    def test_missing_stock_has_all_none_but_still_present(self):
        result = build_fundamentals_by_code("2026-09-22", {"9999"})
        self.assertIn("9999", result)
        self.assertIsNone(result["9999"]["pe_ratio"])

    def test_margin_short_wired_through_with_previous_day_offset(self):
        self._seed_bars("2330", 100.0, 10.0, range(17, 23))
        save_margin_short_rows([
            {"code": "2330", "tradeDate": "2026-09-21", "marginTodayBalance": 800, "marginLimit": 2000, "shortTodayBalance": 200, "shortLimit": 500},
            {"code": "2330", "tradeDate": "2026-09-22", "marginTodayBalance": 999999, "marginLimit": 999999, "shortTodayBalance": 999999, "shortLimit": 999999},
        ])
        result = build_fundamentals_by_code("2026-09-22", {"2330"})
        self.assertAlmostEqual(result["2330"]["short_margin_ratio_pct"], 200 / 800 * 100)

    def test_pbr_industry_avg_computed_when_industry_has_enough_peers(self):
        # 半導體5檔(含2330)，pbr=1..5，平均3.0；電子零組件只有1檔，同類太少不計入
        codes = {f"S{i}" for i in range(1, 5)} | {"2330", "E1"}
        rows = []
        for i in range(1, 5):
            self._seed_bars(f"S{i}", 10.0, 1.0, range(17, 23))
            rows.append({"code": f"S{i}", "tradeDate": "2026-09-22", "pbr": float(i)})
        self._seed_bars("2330", 10.0, 1.0, range(17, 23))
        rows.append({"code": "2330", "tradeDate": "2026-09-22", "pbr": 5.0})
        self._seed_bars("E1", 10.0, 1.0, range(17, 23))
        rows.append({"code": "E1", "tradeDate": "2026-09-22", "pbr": 100.0})
        save_fundamentals_rows(rows)
        industry_by_code = {**{f"S{i}": "半導體" for i in range(1, 5)}, "2330": "半導體", "E1": "電子零組件"}

        result = build_fundamentals_by_code("2026-09-22", codes, industry_by_code=industry_by_code)

        self.assertAlmostEqual(result["2330"]["pbr_industry_avg"], 3.0)  # (1+2+3+4+5)/5
        self.assertIsNone(result["E1"]["pbr_industry_avg"])  # 同類只有1檔，未達MIN_INDUSTRY_PEERS

    def test_pbr_industry_avg_none_without_industry_by_code(self):
        self._seed_bars("2330", 100.0, 10.0, range(17, 23))
        save_fundamentals_rows([{"code": "2330", "tradeDate": "2026-09-22", "pbr": 3.0}])
        result = build_fundamentals_by_code("2026-09-22", {"2330"})
        self.assertIsNone(result["2330"]["pbr_industry_avg"])


if __name__ == "__main__":
    unittest.main()
