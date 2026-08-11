from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from futures_bar_bootstrap import clear_futures_bar_bootstrap_cache, get_resilient_futures_bars
from market_data_hub import MarketDataHub
from otc_index import TW_TZ


def ts(hour: int, minute: int, second: int = 0) -> int:
    return int(datetime(2026, 8, 10, hour, minute, second, tzinfo=TW_TZ).timestamp() * 1000)


def wall_ns(hour: int, minute: int) -> int:
    return int(datetime(2026, 8, 10, hour, minute, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def wall_ns_for_day(day: str, hour: int, minute: int) -> int:
    value = datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+00:00")
    return int(value.timestamp() * 1_000_000_000)


class FakeContracts:
    def get(self, code: str):
        if code == "TXFR1":
            return SimpleNamespace(code="TXFR1", target_code="TXFQ6")
        if code == "TXFH6":
            return SimpleNamespace(code="TXFH6")
        if code == "MXFR1":
            return SimpleNamespace(code="MXFR1", target_code="MXFQ6")
        return None


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


class TrackingApi(FakeApi):
    def __init__(self):
        self.kbar_calls = 0

    def kbars(self, *, contract, start: str, end: str):
        self.kbar_calls += 1
        return super().kbars(contract=contract, start=start, end=end)


class RangeLimitedApi(FakeApi):
    def __init__(self):
        self.ranges: list[tuple[str, str]] = []

    def kbars(self, *, contract, start: str, end: str):
        start_day = datetime.fromisoformat(start)
        end_day = datetime.fromisoformat(end)
        if (end_day - start_day).days > 29:
            raise AssertionError("Kbars range exceeded 30 calendar days")
        self.ranges.append((start, end))
        days = []
        cursor = start_day
        while cursor <= end_day:
            if cursor.weekday() < 5:
                days.append(cursor.date().isoformat())
            cursor += timedelta(days=1)
        return {
            "ts": [wall_ns_for_day(day, 8, 46) for day in days],
            "Open": [100.0] * len(days),
            "High": [102.0] * len(days),
            "Low": [99.0] * len(days),
            "Close": [101.0] * len(days),
            "Volume": [10] * len(days),
        }


class FakeService:
    api = FakeApi()
    state = SimpleNamespace(logged_in=True)
    _target_code = "TXFR1"
    _resolved_futures_code = "TXFQ6"

    def __init__(self):
        self.extra_subscriptions = []

    def ensure_extra_futures_subscription(self, contract):
        self.extra_subscriptions.append(getattr(contract, "target_code", contract.code))
        return True

    @staticmethod
    def get_latest_tick():
        return {"code": "TXFQ6"}


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

    def test_stale_txf_month_uses_active_r1_contract(self):
        result = get_resilient_futures_bars(
            "TXFH6",
            "5m",
            service=FakeService(),
            hub=EmptyHub(),
            now_ms=ts(9, 1, 30),
        )

        self.assertEqual(result["requested_code"], "TXFH6")
        self.assertEqual(result["code"], "TXFQ6")
        self.assertGreater(result["bar_count"], 0)

    def test_mini_taiwan_futures_uses_night_capable_contract(self):
        service = FakeService()
        result = get_resilient_futures_bars(
            "MXFR1",
            "5m",
            service=service,
            hub=EmptyHub(),
            now_ms=ts(9, 1, 30),
        )

        self.assertEqual(result["code"], "MXFQ6")
        self.assertEqual(service.extra_subscriptions, ["MXFQ6"])
        self.assertGreater(result["bar_count"], 0)

    def test_daily_kline_aggregates_history_and_current_session(self):
        result = get_resilient_futures_bars(
            "TXFH6",
            "1d",
            service=FakeService(),
            hub=EmptyHub(),
            now_ms=ts(9, 1, 30),
        )

        self.assertEqual(result["interval"], "1d")
        self.assertEqual(result["history_days"], 180)
        self.assertEqual(result["bar_count"], 1)
        self.assertEqual(result["bars"][0]["open"], 44790.0)
        self.assertEqual(result["bars"][0]["close"], 44810.0)
        self.assertEqual(result["bars"][0]["volume"], 16)


    def test_daily_history_is_split_into_shioaji_30_day_ranges(self):
        api = RangeLimitedApi()
        service = FakeService()
        service.api = api
        result = get_resilient_futures_bars(
            "TXFH6",
            "1d",
            service=service,
            hub=EmptyHub(),
            now_ms=ts(9, 1, 30),
        )

        self.assertEqual(len(api.ranges), 6)
        self.assertTrue(all((datetime.fromisoformat(end) - datetime.fromisoformat(start)).days <= 29 for start, end in api.ranges))
        self.assertGreater(result["bar_count"], 100)

    def test_stock_futures_show_latest_day_session_at_night(self):
        contract = SimpleNamespace(code="NCFR1", target_code="NCFQ6")
        result = get_resilient_futures_bars(
            "NCFR1",
            "5m",
            service=FakeService(),
            hub=EmptyHub(),
            now_ms=ts(23, 0),
            stock_futures_lookup=lambda code: (contract, "NCFQ6") if code == "NCFR1" else None,
        )

        self.assertEqual(result["session"], "day")
        self.assertEqual(result["code"], "NCFQ6")
        self.assertGreater(result["bar_count"], 0)

    def test_stock_futures_history_uses_initialized_subscription_api(self):
        contract = SimpleNamespace(code="NCFR1", target_code="NCFQ6")
        ready_api = TrackingApi()
        result = get_resilient_futures_bars(
            "NCFR1",
            "1m",
            service=FakeService(),
            hub=EmptyHub(),
            now_ms=ts(9, 1, 30),
            stock_futures_lookup=lambda code: (contract, "NCFQ6", ready_api) if code == "NCFR1" else None,
        )

        self.assertGreater(result["bar_count"], 0)
        self.assertEqual(ready_api.kbar_calls, 1)


if __name__ == "__main__":
    unittest.main()
