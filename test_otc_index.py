from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from otc_index import (
    aggregate_1m_to_5m,
    index_name_score,
    is_regular_otc_session,
    normalize_kbars_1m,
    timestamp_to_ms,
)
from otc_index_hub import OtcIndexHub

TW = timezone(timedelta(hours=8))


def tpe_ms(hour: int, minute: int) -> int:
    return int(datetime(2026, 8, 7, hour, minute, tzinfo=TW).timestamp() * 1000)


class OtcIndexHelperTests(unittest.TestCase):
    def test_index_name_score_prefers_main_otc_index(self) -> None:
        self.assertGreater(
            index_name_score("櫃檯買賣發行量加權股價指數", "OTC"),
            9000,
        )
        self.assertGreater(index_name_score("櫃買指數", "OTC"), 8000)
        self.assertEqual(index_name_score("櫃買薪酬指數", "OTC"), 0)
        self.assertLess(index_name_score("發行量加權股價指數", "TSE"), 0)

    def test_timestamp_to_ms_handles_nanoseconds_and_datetimes(self) -> None:
        ms = tpe_ms(9, 0)
        self.assertEqual(timestamp_to_ms(ms * 1_000_000), ms)
        self.assertEqual(timestamp_to_ms(ms), ms)
        self.assertEqual(
            timestamp_to_ms(datetime(2026, 8, 7, 9, 0, tzinfo=TW)),
            ms,
        )

    def test_regular_session_excludes_preopen_and_1330_bucket(self) -> None:
        self.assertFalse(is_regular_otc_session(tpe_ms(8, 59)))
        self.assertTrue(is_regular_otc_session(tpe_ms(9, 0)))
        self.assertTrue(is_regular_otc_session(tpe_ms(13, 29)))
        self.assertFalse(is_regular_otc_session(tpe_ms(13, 30)))

    def test_normalize_kbars_filters_preopen(self) -> None:
        timestamps = [tpe_ms(8, 59), tpe_ms(9, 0), tpe_ms(9, 1)]
        kbars = {
            "ts": [value * 1_000_000 for value in timestamps],
            "Open": [99.0, 100.0, 101.0],
            "High": [100.0, 102.0, 103.0],
            "Low": [98.0, 99.0, 100.0],
            "Close": [99.5, 101.0, 102.0],
            "Volume": [1, 2, 3],
        }
        rows = normalize_kbars_1m(
            kbars,
            trade_date="2026-08-07",
            include_current=False,
            now_ms=tpe_ms(10, 0),
        )
        self.assertEqual([row["ts"] for row in rows], [tpe_ms(9, 0), tpe_ms(9, 1)])

    def test_aggregate_1m_to_5m_preserves_ohlc(self) -> None:
        bars = []
        closes = [101.0, 100.5, 102.0, 103.0, 102.5]
        highs = [101.5, 102.0, 102.5, 104.0, 103.5]
        lows = [99.0, 99.5, 100.0, 101.0, 101.5]
        for index in range(5):
            bars.append({
                "ts": tpe_ms(9, index),
                "open": 100.0 + index,
                "high": highs[index],
                "low": lows[index],
                "close": closes[index],
                "volume": index + 1,
                "tick_count": 1,
            })
        rows = aggregate_1m_to_5m(bars, include_current=False, now_ms=tpe_ms(9, 10))
        self.assertEqual(len(rows), 1)
        bar = rows[0]
        self.assertEqual(bar["ts"], tpe_ms(9, 0))
        self.assertEqual(bar["open"], 100.0)
        self.assertEqual(bar["high"], 104.0)
        self.assertEqual(bar["low"], 99.0)
        self.assertEqual(bar["close"], 102.5)
        self.assertEqual(bar["volume"], 15)


class OtcIndexHubTests(unittest.TestCase):
    def test_seed_and_live_quote_continue_same_day(self) -> None:
        hub = OtcIndexHub()
        seeded_1m = [
            {"ts": tpe_ms(9, 0), "open": 300.0, "high": 301.0, "low": 299.0, "close": 300.5, "volume": 0, "tick_count": 1},
        ]
        seeded_5m = [
            {"ts": tpe_ms(9, 0), "open": 300.0, "high": 302.0, "low": 299.0, "close": 301.0, "volume": 0, "tick_count": 5},
        ]
        hub.seed_today(seeded_1m, seeded_5m, "2026-08-07")
        hub.on_quote({
            "code": "TEST_OTC",
            "close": 305.0,
            "volume": 0,
            "datetime": datetime(2026, 8, 7, 9, 5, 10, tzinfo=TW).isoformat(),
        })
        bars = hub.get_bars_5m(include_current=True)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["ts"], tpe_ms(9, 0))
        self.assertEqual(bars[1]["ts"], tpe_ms(9, 5))
        self.assertEqual(bars[1]["close"], 305.0)


if __name__ == "__main__":
    unittest.main()
