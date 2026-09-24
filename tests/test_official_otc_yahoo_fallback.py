"""上櫃日K Yahoo 備援：櫃買中心擋 Railway、FinMind 付費方案到期時，逐檔用 Yahoo 日K補上櫃。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
import official_daily_bars as module
import stock_groups
from daily_bars_history_backfill import _tw_epoch
from database import get_connection
from official_daily_bars import _save_day

UTC = timezone.utc
TW = timezone(timedelta(hours=8))
DAYS = ("2026-09-21", "2026-09-22", "2026-09-23")


def _bar(code, name, market, day, close):
    return {"stock_code": code, "stock_name": name, "market": market, "time": datetime.fromisoformat(day).replace(tzinfo=UTC),
            "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000}


def _yahoo_payload(closes: dict[str, float]):
    days = sorted(closes)
    values = [closes[day] for day in days]
    return {"chart": {"result": [{
        "timestamp": [_tw_epoch(day, 9, 0) for day in days],
        "indicators": {"quote": [{"open": values, "high": [v + 1 for v in values], "low": [v - 1 for v in values],
                                  "close": values, "volume": [3_000_000] * len(days)}]},
    }], "error": None}}


class OtcYahooFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._yahoo_otc_attempted.clear()
        for code, name in (("8069", "元太"), ("6488", "環球晶"), ("3529", "力旺")):
            _save_day([_bar(code, name, "OTC", day, 100.0) for day in DAYS])
        _save_day([_bar("2330", "台積電", "TSE", day, 1000.0) for day in DAYS])
        _save_day([_bar("9999", "已下市", "OTC", "2026-05-01", 10.0)])  # 最近沒有日K：不打
        self.priority_patch = patch.object(stock_groups, "industry_group_codes", return_value=frozenset({"6488"}))
        self.priority_patch.start()

    def tearDown(self) -> None:
        self.priority_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _fetcher(self, calls, flaky=("3529",)):
        failed_once: set[str] = set()

        def fetch(url, params):
            symbol = url.rsplit("/", 1)[-1]
            code = symbol.split(".")[0]
            calls.append(symbol)
            if code in flaky and code not in failed_once:
                failed_once.add(code)
                raise OSError("HTTP Error 429: Too Many Requests")
            base = 150.0 if code == "6488" else 100.0  # 6488 價格基準跟官方對不上
            closes = {day: base for day in DAYS}
            closes["2026-09-24"] = base + 5
            return _yahoo_payload(closes)
        return fetch

    def _closes(self, code):
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT substr(bar_time, 1, 10) AS d, close, volume FROM bars_1d WHERE stock_code = ?", (code,)
            ).fetchall()
        return {row["d"]: (row["close"], row["volume"]) for row in rows}

    def test_fills_missing_otc_day_with_priority_price_check_and_retry(self) -> None:
        calls: list[str] = []
        result = module._yahoo_otc_fill([date(2026, 9, 24)], delay=0, retry_pause=0, fetcher=self._fetcher(calls),
                                        now=datetime(2026, 9, 24, 15, 0, tzinfo=TW))
        self.assertEqual(calls[0], "6488.TWO")                                   # 43 族群的先補
        self.assertNotIn("9999.TWO", calls)                                      # 下市（最近沒日K）不打
        self.assertNotIn("2330.TW", calls)                                       # 上市不歸這裡
        self.assertEqual(calls.count("3529.TWO"), 2)                             # 429 之後再試一輪
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["mismatched"], ["6488"])
        self.assertEqual(result["failureCount"], 0)
        self.assertEqual(self._closes("8069")["2026-09-24"], (105.0, 3000))     # 補上今天，量換成張
        self.assertEqual(self._closes("8069")["2026-09-23"], (100.0, 1000))     # 已經有的官方日K不動
        self.assertIn("2026-09-24", self._closes("3529"))
        self.assertNotIn("2026-09-24", self._closes("6488"))                     # 價格對不上：整檔不寫
        with get_connection() as connection:
            market = connection.execute("SELECT market FROM stocks WHERE stock_code = '8069'").fetchone()["market"]
        self.assertEqual(market, "OTC")

    def test_only_codes_still_missing_the_day_are_fetched(self) -> None:
        # 上一輪補到一半被部署打斷：8069 已經有 9/24，這輪只補還缺的 6488、3529
        _save_day([_bar("8069", "元太", "OTC", "2026-09-24", 105.0)])
        calls: list[str] = []
        result = module._yahoo_otc_fill([date(2026, 9, 24)], delay=0, retry_pause=0, fetcher=self._fetcher(calls, flaky=()),
                                        now=datetime(2026, 9, 24, 15, 0, tzinfo=TW))
        self.assertEqual(sorted(set(calls)), ["3529.TWO", "6488.TWO"])
        self.assertEqual(result["stocks"], 2)
        calls.clear()
        module._yahoo_otc_attempted.clear()
        again = module._yahoo_otc_fill([date(2026, 9, 24)], delay=0, retry_pause=0, fetcher=self._fetcher(calls, flaky=()),
                                       now=datetime(2026, 9, 24, 19, 0, tzinfo=TW))
        self.assertEqual(calls, ["6488.TWO"])                                  # 只剩價格對不上的那檔還缺
        self.assertEqual(again["inserted"], 0)

    def test_otc_day_complete_needs_97_percent_of_previous_day(self) -> None:
        codes = [f"{6000 + n}" for n in range(110)]
        _save_day([_bar(code, code, "OTC", "2026-09-23", 50.0) for code in codes])
        _save_day([_bar(code, code, "OTC", "2026-09-24", 50.0) for code in codes[:100]])
        self.assertFalse(module.otc_day_complete(date(2026, 9, 24)))            # 前一天 113 檔，今天才 100 檔：還在補
        _save_day([_bar(code, code, "OTC", "2026-09-24", 50.0) for code in codes[100:]])
        self.assertTrue(module.otc_day_complete(date(2026, 9, 24)))             # 110／113 ≥ 97%
        self.assertFalse(module.otc_day_complete(date(2026, 9, 25)))            # 一檔都沒有

    def test_same_day_not_refetched_within_three_hours_and_today_waits_until_1430(self) -> None:
        calls: list[str] = []
        early = module._yahoo_otc_fill([date(2026, 9, 24)], delay=0, retry_pause=0, fetcher=self._fetcher(calls),
                                       now=datetime(2026, 9, 24, 14, 0, tzinfo=TW))
        self.assertIn("skipped", early)                                          # 今天 14:30 前 Yahoo 還不是最終值
        self.assertEqual(calls, [])
        module._yahoo_otc_fill([date(2026, 9, 24)], delay=0, retry_pause=0, fetcher=self._fetcher(calls),
                               now=datetime(2026, 9, 24, 15, 0, tzinfo=TW))
        calls.clear()
        again = module._yahoo_otc_fill([date(2026, 9, 24)], delay=0, retry_pause=0, fetcher=self._fetcher(calls),
                                       now=datetime(2026, 9, 24, 16, 0, tzinfo=TW))
        self.assertIn("skipped", again)                                          # 收集器每小時一輪，3 小時內不重打
        self.assertEqual(calls, [])

    def test_collector_uses_yahoo_only_when_tpex_and_finmind_both_fail(self) -> None:
        twse_row = _bar("2330", "台積電", "TSE", "2026-09-24", 1010.0)
        captured: list[list[date]] = []

        def fake_fill(missing):
            captured.append(list(missing))
            return {"inserted": 7}

        def tpex_blocked(_trade_date):
            raise RuntimeError("HTTP Error 403: Forbidden")

        with patch.object(module, "fetch_twse_day", lambda d: [dict(twse_row, time=datetime.combine(d, datetime.min.time(), tzinfo=UTC))] if d == date(2026, 9, 24) else []), \
                patch.object(module, "fetch_tpex_day", tpex_blocked), \
                patch.object(module, "_finmind_otc_day", lambda d: ([], "FinMind 失敗：HTTPError: HTTP Error 402: Payment Required")), \
                patch.object(module, "_yahoo_otc_fill", fake_fill):
            result = module.download_official_daily_bars(days=60, delay=0, end_date=date(2026, 9, 24), run_triangle_scan=False)
        self.assertEqual(captured, [[date(2026, 9, 24)]])
        self.assertEqual(result["yahoo_otc"], {"inserted": 7})
        self.assertEqual(result["inserted_bars"], 1 + 7)                         # 上市 1 根 + Yahoo 補的 7 根

        # 櫃買被擋：第一天失敗之後這一輪不再打（每天重試三個網址要等十幾秒）
        tpex_calls: list[date] = []

        def tpex_counting(d):
            tpex_calls.append(d)
            raise RuntimeError("HTTP Error 403: Forbidden")

        with patch.object(module, "fetch_twse_day", lambda d: [dict(twse_row, time=datetime.combine(d, datetime.min.time(), tzinfo=UTC))] if d.weekday() < 5 else []), \
                patch.object(module, "fetch_tpex_day", tpex_counting), \
                patch.object(module, "_finmind_otc_day", lambda d: ([], "FinMind 失敗")), \
                patch.object(module, "_yahoo_otc_fill", fake_fill):
            result = module.download_official_daily_bars(days=60, delay=0, end_date=date(2026, 9, 24), run_triangle_scan=False)
        self.assertEqual(len(tpex_calls), 1)
        self.assertEqual(len(captured[-1]), module.YAHOO_OTC_MAX_DATES)       # 只檢查最近 10 個櫃買沒給的交易日
        self.assertEqual(captured[-1][-1], date(2026, 9, 24))
        self.assertTrue(any("略過" in f["error"] and "403" in f["error"] for f in result["source_failures"]))

        captured.clear()
        with patch.object(module, "fetch_twse_day", lambda d: [dict(twse_row, time=datetime.combine(d, datetime.min.time(), tzinfo=UTC))] if d == date(2026, 9, 24) else []), \
                patch.object(module, "fetch_tpex_day", tpex_blocked), \
                patch.object(module, "_finmind_otc_day", lambda d: ([_bar("8069", "元太", "OTC", "2026-09-24", 101.0)], "FinMind 補上櫃 1 檔")), \
                patch.object(module, "_yahoo_otc_fill", fake_fill):
            result = module.download_official_daily_bars(days=60, delay=0, end_date=date(2026, 9, 24), run_triangle_scan=False)
        self.assertEqual(captured, [])                                           # FinMind 有補到就不用 Yahoo
        self.assertIsNone(result["yahoo_otc"])


if __name__ == "__main__":
    unittest.main()
