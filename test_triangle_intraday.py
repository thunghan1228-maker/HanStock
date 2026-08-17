from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import database
from intraday_signal_store import load_latest_signals, save_intraday_signals
from triangle_intraday import evaluate_intraday_candidate, scan_intraday_triangles
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def triangle_bars() -> list[dict]:
    start = datetime(2026, 1, 1)
    bars = []
    for index in range(49):
        upper = 120 - index * 0.30
        lower = 80 + index * 0.30
        phase = (index % 8) / 7
        close = lower + (upper - lower) * (0.18 + 0.64 * phase)
        bars.append({
            "bar_time": (start + timedelta(days=index)).isoformat(),
            "open": close - 0.2,
            "high": min(upper, close + 0.8),
            "low": max(lower, close - 0.8),
            "close": close,
            "volume": 1_000_000,
        })
    for index in (8, 20, 32, 44):
        bars[index]["high"] = 120 - index * 0.30
    for index in (12, 24, 36, 45):
        bars[index]["low"] = 80 + index * 0.30
    return bars


class TriangleIntradayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.temp.name) / "hanstock.db"

    def tearDown(self):
        database.DATABASE_PATH = self.old_path
        self.temp.cleanup()

    def test_intraday_preview_uses_provisional_daily_price_and_volume(self):
        candidate = {"stock_code": "2330", "stock_name": "台積電", "market": "TSE", "bars": triangle_bars()}
        row = evaluate_intraday_candidate(candidate, {
            "open": 100,
            "high": 109,
            "low": 99,
            "close": 108,
            "volume": 2_000_000,
        }, "2026-08-17")
        self.assertIsNotNone(row)
        self.assertIn(row["status"], {"突破待量", "放量突破", "接近突破"})
        self.assertTrue(row["preview"])

    def test_scan_saves_each_intraday_triangle_status_once_per_day(self):
        candidate = {"stock_code": "2330", "stock_name": "台積電", "market": "TSE", "bars": triangle_bars()}
        quote = {"open": 100, "high": 109, "low": 99, "close": 108, "volume": 2_000_000}
        now = datetime(2026, 8, 17, 10, 5, 20, tzinfo=TAIPEI)
        kwargs = {
            "candidate_loader": lambda _: [candidate],
            "quote_loader": lambda _: {"2330": quote},
        }
        first = scan_intraday_triangles(now, **kwargs)
        second = scan_intraday_triangles(now.replace(minute=10), **kwargs)
        self.assertEqual(first["summary"]["inserted_signal_count"], 1)
        self.assertEqual(second["summary"]["inserted_signal_count"], 0)
        stored = load_latest_signals("2026-08-17", limit=20)
        self.assertEqual(len(stored), 1)
        self.assertTrue(stored[0]["kind"].startswith("triangle"))

    def test_triangle_signal_kinds_are_independently_once_per_day(self):
        base = {
            "tradeDate": "2026-08-17", "ticker": "2330", "name": "台積電",
            "groupName": "日線三角收斂", "price": 100, "note": "測試",
        }
        rows = save_intraday_signals([
            {**base, "kind": "triangleNearBreakout", "label": "接近突破", "barTs": 1_786_932_000_000},
            {**base, "kind": "triangleNearBreakout", "label": "接近突破", "barTs": 1_786_932_300_000},
            {**base, "kind": "triangleVolumeBreakout", "label": "放量突破", "barTs": 1_786_932_600_000},
        ])
        self.assertEqual([row["kind"] for row in rows], ["triangleNearBreakout", "triangleVolumeBreakout"])


if __name__ == "__main__":
    unittest.main()
