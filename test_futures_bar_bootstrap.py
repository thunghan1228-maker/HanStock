from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from futures_bar_bootstrap import clear_futures_bar_bootstrap_cache, get_resilient_futures_bars
from market_data_hub import MarketDataHub
from otc_index import TW_TZ


def ts(hour: int, minute: int, second: int = 0) -> int:
    return int(datetime(2026, 8, 10, hour, minute, second, tzinfo=TW_TZ).timestamp() * 1000)


def wall_ns(hour: int, minute: int) -> int:
    return int(datetime(2026, 8, 10, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


class FakeContracts:
    def get(self, code: str):
        return SimpleNamespace(code="TXFH6") if code.startswith("TXF") else None


class FakeApi:
    contracts = FakeContracts()

    def kbars(self, *, contract, start: str, end: str):
        closes = [
            (8, 46), (8, 47), (8, 48), (8, 49), (8, 50),
            (8, 51), (8, 52), (8, 53), (8, 54), (8, 55),
            (8, 56), (8, 57), (8, 58), (8, 59), (9, 0), (9, 1),
        ]
        size = len(closes)
        return {
            "ts": [wall_ns(hour, minute) for hour, minute in closes],
            "Open": [44790 + index for index in range(size)],
            "High": [44800 + index for index in range(size)],
            "Low": [44780 + index for index in range(size)],
            "Close": [44795 + index for index in range(size)],
            "Volume": [1] * size,
        }


class FakeService:
    api = FakeApi()
    state = SimpleNamespace(logged_in=True)
    _target_code = "TXFR1"
    _resolved_futures_code = "TXFH6"

    @staticmethod
    def get_latest_tick():
        return {"code": "TXFH6"}


class EmptyHub:
    @staticmethod
    def get_live_futures_bars(code: str):
        return []

    @staticmethod
    def get_live_futures_bars_1m(code: str):
        return []


class FuturesBarTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_futures_bar_bootstrap_cache()

    def test_futures_ticks_form_independent_five_minute_bars(self):
        hub = MarketDataHub()
        hub.on_futures_tick({"code": "TXFH6", "close": 44800.0, "volume": 2, "tick_time": "2026-08-10T08:45:01+08:00"})
        hub.on_futures_tick({"code": "TXFH6", "close": 44810.0, "volume": 3, "tick_time": "2026-08-10T08:49:59+08:00"})
        hub.on_futures_tick({"code": "TXFH6", "close": 44820.0, "volume": 1, "tick_time": "2026-08-10T08:50:01+08:00"})

        bars = hub.get_live_futures_bars("TXFH6")
        self.assertEqual(len(bars), 2)
        self.assertEqual((bars[0]["open"], bars[0]["close"], bars[0]["volume"]), (44800.0, 44810.0, 5))
        self.assertEqual(bars[1]["open"], 44820.0)

    def test_day_session_bootstrap_starts_at_0845(self):
        result = get_resilient_futures_bars(
            "TXFH6",
            "5m",
            service=FakeService(),
            hub=EmptyHub(),
            now_ms=ts(9, 1, 30),
        )

        self.assertEqual(result["session"], "day")
        self.assertTrue(result["bootstrap"]["history_ok"])
        self.assertEqual(
            [datetime.fromtimestamp(bar["ts"] / 1000, TW_TZ).strftime("%H:%M") for bar in result["bars"]],
            ["08:45", "08:50", "08:55"],
        )


if __name__ == "__main__":
    unittest.main()
