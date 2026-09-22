from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
from otc_index_store import load_index_bars_5m, save_index_bars_5m

TW = timezone(timedelta(hours=8))


def bar(day: date, hour: int, minute: int, close: float = 100.0) -> dict:
    ts = int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=TW).timestamp() * 1000)
    return {"ts": ts, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 3, "tick_count": 2}


class OtcIndexStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_round_trip_is_sorted_and_filtered_by_date_range(self) -> None:
        d0, d1 = date(2026, 9, 21), date(2026, 9, 22)
        self.assertEqual(save_index_bars_5m([bar(d1, 9, 5, 200.0), bar(d0, 9, 0, 100.0)]), 2)

        rows = load_index_bars_5m("2026-09-16", "2026-09-22")

        self.assertEqual([r["close"] for r in rows], [100.0, 200.0])
        self.assertEqual(rows[0]["ts"], bar(d0, 9, 0)["ts"])
        self.assertEqual(rows[1]["high"], 201.0)
        self.assertEqual(rows[1]["tick_count"], 1)
        self.assertEqual([r["close"] for r in load_index_bars_5m("2026-09-22", "2026-09-22")], [200.0])
        self.assertEqual(load_index_bars_5m("2026-09-23", "2026-09-30"), [])

    def test_ignores_bars_outside_regular_session_and_upserts_same_bucket(self) -> None:
        d1 = date(2026, 9, 22)
        self.assertEqual(save_index_bars_5m([bar(d1, 13, 30), bar(d1, 8, 55), {"ts": "bad"}]), 0)

        save_index_bars_5m([bar(d1, 9, 0, 100.0)])
        save_index_bars_5m([bar(d1, 9, 0, 105.0)])

        rows = load_index_bars_5m("2026-09-22", "2026-09-22")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 105.0)


if __name__ == "__main__":
    unittest.main()
