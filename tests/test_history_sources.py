from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import history_sources as module
from history_sources import (
    fetch_minute_bars_chain,
    fetch_yahoo_minute_bars,
    parse_finmind_minute_rows,
    parse_yahoo_chart,
    probe_history_sources,
)

TW = timezone(timedelta(hours=8))


def ts(day: str, hour: int, minute: int) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=TW).timestamp() * 1000)


class FinMindParserTests(unittest.TestCase):
    def test_close_time_labels_shift_back_one_minute_and_shares_become_lots(self) -> None:
        rows = [
            {"date": "2026-09-22", "minute": "09:01:00", "open": 100, "max": 101, "min": 99, "close": 100.5, "volume": 12000},
            {"date": "2026-09-22", "minute": "09:02:00", "open": 100.5, "max": 102, "min": 100, "close": 101, "volume": 3000},
            {"date": "2026-09-22", "minute": "13:31:00", "open": 101, "max": 101, "min": 101, "close": 101, "volume": 1000},
            {"date": "2026-09-21", "minute": "09:01:00", "open": 90, "max": 91, "min": 89, "close": 90.5, "volume": 1000},
        ]

        bars = parse_finmind_minute_rows(rows, "2026-09-22", "2026-09-22")

        self.assertEqual([b["ts"] for b in bars], [ts("2026-09-22", 9, 0), ts("2026-09-22", 9, 1)])
        self.assertEqual(bars[0]["volume"], 12)
        self.assertEqual(bars[0]["high"], 101.0)
        self.assertEqual(bars[1]["close"], 101.0)

    def test_labels_that_include_0900_are_bar_start_times(self) -> None:
        rows = [
            {"date": "2026-09-22", "minute": "09:00:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
            {"date": "2026-09-22", "minute": "09:01:00", "open": 100.5, "high": 102, "low": 100, "close": 101, "volume": 1000},
        ]

        bars = parse_finmind_minute_rows(rows, "2026-09-22", "2026-09-22")

        self.assertEqual([b["ts"] for b in bars], [ts("2026-09-22", 9, 0), ts("2026-09-22", 9, 1)])

    def test_garbage_rows_are_ignored(self) -> None:
        rows = [{"date": "2026-09-22", "minute": "bad"}, "nope", {"date": "2026-09-22", "minute": "09:01:00", "open": 0, "max": 0, "min": 0, "close": 0}]
        self.assertEqual(parse_finmind_minute_rows(rows, "2026-09-22", "2026-09-22"), [])
        self.assertEqual(parse_finmind_minute_rows(None, "2026-09-22", "2026-09-22"), [])


class YahooParserTests(unittest.TestCase):
    def test_chart_payload_becomes_session_bars_with_lots(self) -> None:
        stamps = [ts("2026-09-22", 9, 0) // 1000, ts("2026-09-22", 9, 1) // 1000, ts("2026-09-22", 13, 30) // 1000]
        payload = {"chart": {"result": [{
            "timestamp": stamps,
            "indicators": {"quote": [{
                "open": [100.0, None, 101.0], "high": [101.0, None, 101.0], "low": [99.0, None, 101.0],
                "close": [100.5, None, 101.0], "volume": [25000, None, 500000],
            }]},
        }], "error": None}}

        bars = parse_yahoo_chart(payload, "2026-09-22", "2026-09-22")

        self.assertEqual([b["ts"] for b in bars], [ts("2026-09-22", 9, 0)])  # None 那根跳過，13:30 不在正式盤
        self.assertEqual(bars[0]["volume"], 25)
        self.assertEqual(bars[0]["close"], 100.5)

    def test_chart_error_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_yahoo_chart({"chart": {"result": None, "error": {"code": "Not Found"}}}, "2026-09-22", "2026-09-22")

    def test_unknown_market_tries_tw_then_two(self) -> None:
        calls: list[str] = []

        def fetcher(url, params):
            calls.append(url)
            if url.endswith("3532.TW"):
                raise RuntimeError("404")
            return {"chart": {"result": [{
                "timestamp": [ts("2026-09-22", 9, 0) // 1000],
                "indicators": {"quote": [{"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1000]}]},
            }], "error": None}}

        bars = fetch_yahoo_minute_bars("3532", "2026-09-22", "2026-09-22", fetcher=fetcher)

        self.assertEqual(len(bars), 1)
        self.assertEqual([url.rsplit("/", 1)[1] for url in calls], ["3532.TW", "3532.TWO"])

    def test_known_otc_market_only_tries_two(self) -> None:
        calls: list[str] = []

        def fetcher(url, params):
            calls.append(url)
            return {"chart": {"result": [], "error": None}}

        self.assertEqual(fetch_yahoo_minute_bars("6488", "2026-09-22", "2026-09-22", market="OTC", fetcher=fetcher), [])
        self.assertEqual([url.rsplit("/", 1)[1] for url in calls], ["6488.TWO"])


class ChainTests(unittest.TestCase):
    def test_finmind_failure_falls_through_to_yahoo(self) -> None:
        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                raise RuntimeError("finmind down")
            return {"chart": {"result": [{
                "timestamp": [ts("2026-09-22", 9, 0) // 1000],
                "indicators": {"quote": [{"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1000]}]},
            }], "error": None}}

        with patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"}):
            bars, source = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)

        self.assertEqual(source, "yahoo")
        self.assertEqual(len(bars), 1)

    def test_finmind_wins_when_it_has_data(self) -> None:
        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                self.assertEqual(params["data_id"], "2330")
                return {"status": 200, "data": [
                    {"date": "2026-09-22", "minute": "09:01:00", "open": 100, "max": 101, "min": 99, "close": 100.5, "volume": 1000},
                ]}
            raise AssertionError("FinMind 有資料時不該再打 Yahoo")

        with patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"}):
            bars, source = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", fetcher=fetcher)

        self.assertEqual(source, "finmind")
        self.assertEqual(bars[0]["ts"], ts("2026-09-22", 9, 0))

    def test_no_token_skips_finmind_and_empty_everywhere_returns_none(self) -> None:
        calls: list[str] = []

        def fetcher(url, params):
            calls.append(url)
            return {"chart": {"result": [], "error": None}}

        with patch.dict(os.environ, {"FINMIND_TOKEN": ""}):
            bars, source = fetch_minute_bars_chain("2330", "2026-09-22", "2026-09-22", market="TSE", fetcher=fetcher)

        self.assertEqual((bars, source), ([], None))
        self.assertTrue(all(url.startswith(module.YAHOO_CHART_URL) for url in calls))

    def test_probe_reports_each_source(self) -> None:
        def fetcher(url, params):
            if url.startswith(module.FINMIND_DATA_URL):
                return {"status": 200, "data": []}
            raise RuntimeError("yahoo 429")

        with patch.dict(os.environ, {"FINMIND_TOKEN": "dummy"}):
            probe = probe_history_sources("2330", "2026-09-22", market="TSE", fetcher=fetcher)

        self.assertFalse(probe["finmind"]["ok"])
        self.assertEqual(probe["finmind"]["bars"], 0)
        self.assertIn("yahoo 429", probe["yahoo"]["error"])
        status = module.history_sources_status()
        self.assertEqual(status["order"], ["finmind", "yahoo"])
        self.assertIn("yahoo 429", status["yahoo"]["lastError"])


if __name__ == "__main__":
    unittest.main()
