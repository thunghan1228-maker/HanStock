from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import stock_history_service as module
from history_quota import HistoryQuotaGate
from stock_history_service import _fetch_history, clear_stock_history_cache

TW = timezone(timedelta(hours=8))
TRADE_DATE = "2026-09-18"
START_DATE = "2026-09-14"
NOW_MS = int(datetime(2026, 9, 18, 14, 0, tzinfo=TW).timestamp() * 1000)  # 收盤後，今天的歷史可以含進來


def ts(day: str, hour: int, minute: int) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=TW).timestamp() * 1000)


def minute_bars(day: str, count: int = 10) -> list[dict]:
    return [{"ts": ts(day, 9, i), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10, "tick_count": 1}
            for i in range(count)]


class ExhaustedApi:
    def usage(self):
        return {"limit_bytes": 500, "remaining_bytes": 0}

    def kbars(self, **kwargs):
        raise AssertionError("額度用完時不該再打 kbars")


class BrokenApi:
    def usage(self):
        return {"limit_bytes": 500, "remaining_bytes": 400}

    def kbars(self, **kwargs):
        raise RuntimeError("shioaji timeout")


def service_with(api):
    svc = SimpleNamespace(api=api, state=SimpleNamespace(logged_in=True))
    svc._resolve_stock_contract = lambda code: object()
    return svc


class StockHistoryFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_stock_history_cache()
        self.quota = patch.object(module, "history_quota", HistoryQuotaGate())
        self.quota.start()
        self.market = patch.object(module, "stock_market", lambda code: "TSE")
        self.market.start()

    def tearDown(self) -> None:
        self.quota.stop()
        self.market.stop()
        clear_stock_history_cache()

    def test_quota_exhausted_uses_fallback_chain_instead_of_giving_up(self) -> None:
        chain_calls: list[tuple] = []

        def chain(code, start, end, *, market=None, fetcher=None):
            chain_calls.append((code, start, end, market))
            return minute_bars("2026-09-17") + minute_bars(TRADE_DATE), "finmind"

        with patch.object(module, "fetch_minute_bars_chain", chain):
            entry = _fetch_history("2330", TRADE_DATE, START_DATE, service=service_with(ExhaustedApi()), now_ms=NOW_MS, monotonic_fn=lambda: 100.0)

        self.assertTrue(entry.ok)
        self.assertEqual(entry.source, "finmind")
        self.assertIsNone(entry.error)
        self.assertEqual(chain_calls, [("2330", START_DATE, TRADE_DATE, "TSE")])
        self.assertEqual(len(entry.bars_5m), 4)  # 兩天各 10 根 1 分 K → 各 2 根 5 分 K
        self.assertEqual(len(entry.bars_1m), 20)

    def test_kbars_failure_uses_fallback_and_reports_source(self) -> None:
        with patch.object(module, "fetch_minute_bars_chain", lambda *a, **k: (minute_bars(TRADE_DATE), "yahoo")):
            entry = _fetch_history("2330", TRADE_DATE, START_DATE, service=service_with(BrokenApi()), now_ms=NOW_MS, monotonic_fn=lambda: 100.0)

        self.assertTrue(entry.ok)
        self.assertEqual(entry.source, "yahoo")

    def test_fallback_failure_keeps_quota_error_and_backs_off(self) -> None:
        calls = []

        def chain(*args, **kwargs):
            calls.append(1)
            return [], None

        clock = {"now": 100.0}
        with patch.object(module, "fetch_minute_bars_chain", chain):
            first = _fetch_history("2330", TRADE_DATE, START_DATE, service=service_with(ExhaustedApi()), now_ms=NOW_MS, monotonic_fn=lambda: clock["now"])
            clock["now"] = 200.0  # 失敗快取 30 秒已過，但備援冷卻 300 秒還沒到
            second = _fetch_history("2330", TRADE_DATE, START_DATE, service=service_with(ExhaustedApi()), now_ms=NOW_MS, monotonic_fn=lambda: clock["now"])
            clock["now"] = 500.0
            _fetch_history("2330", TRADE_DATE, START_DATE, service=service_with(ExhaustedApi()), now_ms=NOW_MS, monotonic_fn=lambda: clock["now"])

        self.assertFalse(first.ok)
        self.assertIn("history_quota_exhausted", first.error)
        self.assertFalse(second.ok)
        self.assertEqual(len(calls), 2)  # 第二次在冷卻內不重打，第三次冷卻過了才再試

    def test_shioaji_success_never_touches_fallback(self) -> None:
        class GoodApi:
            def usage(self):
                return {"limit_bytes": 500, "remaining_bytes": 400}

            def kbars(self, *, contract, start, end):
                closes = [datetime(2026, 9, 18, 9, i, tzinfo=TW) for i in range(1, 11)]
                return {"ts": closes, "Open": [100.0] * 10, "High": [101.0] * 10, "Low": [99.0] * 10, "Close": [100.5] * 10, "Volume": [10] * 10}

        with patch.object(module, "fetch_minute_bars_chain", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該呼叫備援"))):
            entry = _fetch_history("2330", TRADE_DATE, START_DATE, service=service_with(GoodApi()), now_ms=NOW_MS, monotonic_fn=lambda: 100.0)

        self.assertTrue(entry.ok)
        self.assertEqual(entry.source, "shioaji")


if __name__ == "__main__":
    unittest.main()
