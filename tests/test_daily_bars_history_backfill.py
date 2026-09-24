"""族群個股日K歷史回補（Yahoo 日K為主、FinMind 備援），讓 MA240／均線分數算得出來。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import brew_launch
import daily_bars_history_backfill as module
import database
from database import get_connection
from official_daily_bars import _save_day

UTC = timezone.utc
FAKE_GROUPS = {"電子紙": [("8069", "元太")], "股期標的": [("2330", "台積電")]}


def _bar(code, name, market, day, close):
    return {"stock_code": code, "stock_name": name, "market": market, "time": datetime.fromisoformat(day).replace(tzinfo=UTC),
            "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000}


def _yahoo_payload(closes: dict[str, float], volume_shares=2_000_000, splits=None):
    days = sorted(closes)
    values = [closes[day] for day in days]
    result = {
        "timestamp": [module._tw_epoch(day, 9, 0) for day in days],  # Yahoo 日K的時間戳是當天 09:00（台北）
        "indicators": {"quote": [{
            "open": values, "high": [v + 2 for v in values], "low": [v - 2 for v in values], "close": values,
            "volume": [volume_shares] * len(days),
        }]},
    }
    if splits:
        result["events"] = {"splits": splits}
    return {"chart": {"result": [result], "error": None}}


class FinMindHttpError(Exception):
    code = 402


class GroupHistoryBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.groups_patch = patch.object(module, "STOCK_GROUPS", FAKE_GROUPS)
        self.groups_patch.start()
        self.env_patch = patch.dict(os.environ, {"FINMIND_TOKEN": ""})
        self.env_patch.start()
        # 兩檔都已經在 stocks 表（官方收集器建的）；09/22 已經完整，09/23 只有 2330（上櫃的 8069 缺）
        _save_day([_bar("2330", "台積電", "TSE", "2026-09-22", 1000), _bar("8069", "元太", "OTC", "2026-09-22", 200)])
        _save_day([_bar("2330", "台積電", "TSE", "2026-09-23", 1010)])

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.groups_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _fetcher(self, calls, *, closes=None, fail_yahoo=(), fail_finmind=()):
        # Yahoo 逐檔日K：09/22 跟官方差 0.5%（同一個價格基準），09/23 是本地缺的那天
        closes = closes or {"2026-09-22": 201.0, "2026-09-23": 210.0}

        def fetch(url, params):
            if "finance.yahoo.com" in url:
                symbol = url.rsplit("/", 1)[-1]
                calls.append(("yahoo", symbol))
                self.assertEqual(params["interval"], "1d")
                if symbol.split(".")[0] in fail_yahoo:
                    raise OSError("HTTP Error 429: Too Many Requests")
                return _yahoo_payload(closes)
            code = params["data_id"]
            calls.append(("finmind", code))
            if code in fail_finmind:
                raise FinMindHttpError("HTTP Error 402: Payment Required")
            return {"data": [
                {"date": day, "stock_id": code, "open": close, "max": close + 2, "min": close - 2, "close": close, "Trading_Volume": 3_000_000}
                for day, close in closes.items()
            ]}
        return fetch

    def _bars(self, code):
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT substr(bar_time, 1, 10) AS d, close, volume FROM bars_1d WHERE stock_code = ? ORDER BY bar_time", (code,)
            ).fetchall()
        return {row["d"]: (row["close"], row["volume"]) for row in rows}

    def _run(self, calls, **kwargs):
        fetcher = self._fetcher(calls, **{k: v for k, v in kwargs.items() if k in {"closes", "fail_yahoo", "fail_finmind"}})
        return module.backfill_group_history(today=date(2026, 9, 24), delay=0, retry_delay=0, fetcher=fetcher)

    def test_fetches_only_stocks_short_of_history_and_keeps_existing(self) -> None:
        calls: list[tuple[str, str]] = []
        with patch.object(module, "MIN_BARS", 2):  # 8069 只有 1 根 → 要抓（2330 只在股期標的，不在 43 族群，不抓）
            result = self._run(calls)
        self.assertEqual(calls, [("yahoo", "8069.TWO")])                   # 上櫃用 .TWO，沒 token 不打 FinMind
        self.assertEqual(result["requestedStocks"], 1)
        self.assertEqual(result["insertedBars"], 1)                            # 09/22 已經有，只新增 09/23
        self.assertEqual(result["sourceCounts"], {"yahoo": 1, "finmind": 0})
        self.assertEqual(result["failureCount"], 0)
        self.assertEqual(result["stillShortCount"], 0)
        self.assertEqual(self._bars("8069")["2026-09-23"], (210.0, 2000))    # 補上缺的那天，量換成張
        self.assertEqual(self._bars("8069")["2026-09-22"], (200.0, 1000))    # 已經有的官方日K不覆蓋
        with get_connection() as connection:
            markets = {row["stock_code"]: row["market"] for row in connection.execute("SELECT stock_code, market FROM stocks").fetchall()}
        self.assertEqual(markets, {"2330": "TSE", "8069": "OTC"})             # 市場不會被改掉

    def test_only_43_group_stocks_and_known_codes_are_requested(self) -> None:
        calls: list[tuple[str, str]] = []
        groups = dict(FAKE_GROUPS, 其他=[("9999", "不在stocks表")])
        with patch.object(module, "STOCK_GROUPS", groups), patch.object(module, "MIN_BARS", 99):
            result = self._run(calls)
        # 2330 只在股期標的 → 不在 43 族群；9999 不在 stocks 表 → 不抓；8069 在族群且在 stocks 表 → 抓
        self.assertEqual(calls, [("yahoo", "8069.TWO")])
        self.assertEqual(result["stillShort"], ["8069"])                      # 補完還不到 99 根：列出來，但不算失敗
        self.assertEqual(result["failureCount"], 0)

    def test_price_mismatch_with_official_bars_skips_the_stock(self) -> None:
        calls: list[tuple[str, str]] = []
        with patch.object(module, "MIN_BARS", 99):
            result = self._run(calls, closes={"2026-09-22": 250.0, "2026-09-23": 260.0})  # 跟官方 200 差 25%
        self.assertEqual(result["mismatched"], ["8069"])
        self.assertEqual(result["insertedBars"], 0)
        self.assertNotIn("2026-09-23", self._bars("8069"))

    def test_yahoo_failure_is_retried_then_reported(self) -> None:
        calls: list[tuple[str, str]] = []
        with patch.object(module, "MIN_BARS", 99):
            result = self._run(calls, fail_yahoo={"8069"})
        self.assertEqual(calls, [("yahoo", "8069.TWO"), ("yahoo", "8069.TWO")])  # 重試一次
        self.assertEqual(result["failureCount"], 1)
        self.assertEqual(result["failures"][0]["code"], "8069")

    def test_finmind_is_fallback_and_stops_after_first_error(self) -> None:
        groups = {"電子紙": [("8069", "元太"), ("6488", "環球晶")], "IC設計": [("3529", "力旺")]}
        _save_day([_bar("6488", "環球晶", "OTC", "2026-09-22", 200), _bar("3529", "力旺", "OTC", "2026-09-22", 200)])
        calls: list[tuple[str, str]] = []
        with patch.dict(os.environ, {"FINMIND_TOKEN": "test-token"}), patch.object(module, "STOCK_GROUPS", groups), \
                patch.object(module, "MIN_BARS", 99):
            result = self._run(calls, fail_yahoo={"3529", "6488", "8069"}, fail_finmind={"6488"})
        finmind_calls = [code for source, code in calls if source == "finmind"]
        self.assertEqual(finmind_calls, ["3529", "6488"])                    # 3529 FinMind 救回；6488 回 402 之後 8069 不再打
        self.assertEqual(result["sourceCounts"], {"yahoo": 0, "finmind": 1})
        self.assertEqual(self._bars("3529")["2026-09-23"], (210.0, 3000))
        self.assertEqual(sorted(item["code"] for item in result["failures"]), ["6488", "8069"])
        self.assertIn("402", result["finmindError"])

    def test_parse_yahoo_daily_undoes_split_adjustment_and_skips_no_trade_days(self) -> None:
        split_ts = module._tw_epoch("2026-09-23", 9, 0)
        payload = _yahoo_payload({"2026-09-21": 50.0, "2026-09-22": 100.0, "2026-09-23": 105.0}, volume_shares=4_000_000,
                                 splits={str(split_ts): {"date": split_ts, "numerator": 2, "denominator": 1, "splitRatio": "2:1"}})
        quote_block = payload["chart"]["result"][0]["indicators"]["quote"][0]
        quote_block["volume"][0] = 0                                           # 09/21 沒成交（停牌）
        days = module.parse_yahoo_daily(payload, "2026-09-01", "2026-09-23")
        self.assertNotIn("2026-09-21", days)                                   # 量 0 不算一根
        self.assertEqual(days["2026-09-22"]["close"], 200.0)                  # 除權前：價格乘回 2、量除 2
        self.assertEqual(days["2026-09-22"]["volume"], 2000)
        self.assertEqual(days["2026-09-23"]["close"], 105.0)                  # 除權當天起不用調
        self.assertEqual(days["2026-09-23"]["volume"], 4000)
        self.assertEqual(module.parse_yahoo_daily(payload, "2026-09-23", "2026-09-23").keys(), {"2026-09-23"})

    def test_run_once_needs_no_finmind_token_and_marks_done_only_without_failures(self) -> None:
        with patch.object(module, "backfill_group_history", lambda: {"insertedBars": 5, "failureCount": 1, "failures": [{"code": "8069", "error": "x"}]}):
            module._run_once()                                                 # 沒有 FINMIND_TOKEN 也照跑（Yahoo 不用 token）
        self.assertFalse(module.backfill_state()["done"])                     # 有失敗 → 下次開機重試
        with patch.object(module, "backfill_group_history", lambda: {"insertedBars": 7, "failureCount": 0, "failures": []}):
            module._run_once()
        state = module.backfill_state()
        self.assertTrue(state["done"])
        self.assertEqual(state["result"]["insertedBars"], 7)
        with patch.object(module, "backfill_group_history", lambda: self.fail("done 之後不該再跑")):
            module._run_once()

    def test_brew_launch_payload_carries_backfill_status_and_cache_clears(self) -> None:
        with patch.object(brew_launch, "compute_brew_launch", lambda session=None: {"status": "ok", "stocks": {}}):
            brew_launch.clear_cache()
            payload = brew_launch.get_brew_launch()
            self.assertIn("historyBackfill", payload)
            self.assertFalse(payload["historyBackfill"]["done"])
            self.assertIsNotNone(brew_launch._cache["value"])
            brew_launch.clear_cache()
            self.assertIsNone(brew_launch._cache["value"])


if __name__ == "__main__":
    unittest.main()
