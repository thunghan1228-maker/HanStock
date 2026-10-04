"""日K補到三年：上市逐日補（休市記下來、失敗會收工）、上櫃從鏡像補＋反推還原事件、官方還原表、推測事件、一輪流程。"""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import bars_history as module
import database
import price_adjust

UTC = timezone.utc


def tse_rows(day: date, prices: dict[str, float]) -> list[dict]:
    return [{"stock_code": code, "stock_name": code, "market": "TSE", "time": datetime.combine(day, datetime.min.time(), tzinfo=UTC),
             "open": p, "high": p, "low": p, "close": p, "volume": 10} for code, p in prices.items()]


def filler(n: int = 500) -> dict[str, float]:
    """湊滿上市 500 檔，讓那天算「上市收齊」。"""
    return {f"{1000 + i}": 10.0 for i in range(n)}


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def bar_count(self, code: str | None = None) -> int:
        with database.get_connection() as c:
            if code:
                return c.execute("SELECT COUNT(*) FROM bars_1d WHERE stock_code = ?", (code,)).fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM bars_1d").fetchone()[0]


class TseTests(BaseCase):
    def test_backfill_marks_holidays_and_skips_done_days(self) -> None:
        calls: list[date] = []

        def fetcher(day: date) -> list[dict]:
            calls.append(day)
            if day == date(2024, 2, 28):   # 和平紀念日：證交所回空
                return []
            return tse_rows(day, {**filler(), "2330": 600.0})

        result = module.backfill_tse(date(2024, 2, 26), date(2024, 2, 29), fetcher=fetcher, delay=0, sleep=lambda s: None)
        self.assertEqual(result["todo"], 4)
        self.assertEqual(result["closed"], 1)
        self.assertEqual(self.bar_count("2330"), 3)
        calls.clear()
        again = module.backfill_tse(date(2024, 2, 26), date(2024, 2, 29), fetcher=fetcher, delay=0, sleep=lambda s: None)
        self.assertEqual(again["todo"], 0)          # 收齊的、記下的休市日都不再問
        self.assertEqual(calls, [])

    def test_backfill_stops_after_too_many_failures(self) -> None:
        def fetcher(day: date) -> list[dict]:
            raise RuntimeError("FOR SECURITY REASONS")

        with patch.object(module, "TWSE_MAX_FAILURES", 3):
            result = module.backfill_tse(date(2024, 3, 4), date(2024, 3, 29), fetcher=fetcher, delay=0, sleep=lambda s: None)
        self.assertEqual(result["failures"], 3)
        self.assertIn("stopped", result)


class OtcMirrorTests(BaseCase):
    def mirror(self, months: dict[str, dict]) -> callable:
        index = {"months": {ym: {"days": len(m["days"]), "first": min(m["days"]), "last": max(m["days"])} for ym, m in months.items()}}
        files = {"quotes/index.json": json.dumps(index).encode()}
        for ym, m in months.items():
            files[f"quotes/{ym}.json.gz"] = gzip.compress(json.dumps(m).encode())

        def raw(url: str) -> bytes:
            name = url.split("/tpex/", 1)[1].split("?", 1)[0]
            if name not in files:
                raise FileNotFoundError(name)
            return files[name]

        return raw

    def test_import_and_quote_events(self) -> None:
        days = [date(2024, 1, 22), date(2024, 1, 23), date(2024, 1, 24), date(2024, 1, 25), date(2024, 1, 26), date(2024, 1, 29),
                date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 2), date(2024, 2, 5)]
        for d in days:   # 上市那幾天都有開盤
            database_rows = tse_rows(d, filler())
            from official_daily_bars import _save_day

            _save_day(database_rows)
        jan = {"month": "2024-01", "days": {}}
        feb = {"month": "2024-02", "days": {}}
        for d in days:
            rows = [["6488", "環球晶", 579.0, 584.0, 574.0, 579.0, 0.0, 679837]]
            if d <= date(2024, 1, 24):
                rows.append(["3064", "泰偉", 10.65, 10.65, 10.65, 10.65, 0.0, 1000])   # 1/25 起停止買賣
            if d == date(2024, 2, 5):
                rows.append(["3064", "泰偉", 31.95, 32.2, 31.95, 32.2, -3.3, 8170000])   # 減資恢復
            (jan if d.month == 1 else feb)["days"][d.isoformat()] = rows
        raw = self.mirror({"2024-01": jan, "2024-02": feb})
        result = module.import_otc_from_mirror(date(2024, 1, 1), date(2024, 2, 29), raw=raw)
        self.assertIsNone(result["error"])
        self.assertEqual(result["monthsDone"], 2)
        self.assertEqual(self.bar_count("6488"), 11)
        self.assertEqual(self.bar_count("3064"), 4)
        with database.get_connection() as c:
            vol = c.execute("SELECT volume FROM bars_1d WHERE stock_code = '3064' AND bar_time LIKE '2024-02-05%'").fetchone()[0]
            market = c.execute("SELECT market FROM stocks WHERE stock_code = '3064'").fetchone()[0]
        self.assertEqual((vol, market), (8170, "OTC"))      # 股 → 張
        events = price_adjust.load_events()
        self.assertEqual(list(events), ["3064"])
        self.assertAlmostEqual(events["3064"][0][1], 35.5 / 10.65, places=6)
        # 鏡像沒變：下一輪整個月跳過
        again = module.import_otc_from_mirror(date(2024, 1, 1), date(2024, 2, 29), raw=raw)
        self.assertEqual(again["months"], 0)

    def test_index_unreachable(self) -> None:
        def raw(url: str) -> bytes:
            raise OSError("down")

        result = module.import_otc_from_mirror(date(2024, 1, 1), date(2024, 2, 29), raw=raw)
        self.assertIn("鏡像索引抓不到", result["error"])


class EventTests(BaseCase):
    def test_official_events_and_inference(self) -> None:
        twse = {
            module.TWSE_REDUCTION_URL: {"stat": "OK", "fields": ["恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價"],
                                        "data": [["113/01/22", "3432", "台端", "10.65", "19.69"]]},
            module.TWSE_PAR_URL: {"stat": "OK", "fields": ["恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價"],
                                  "data": [["113/11/11", "8476", "台境", "58.80", "29.40"]]},
        }

        def fetcher(url: str, params: dict) -> dict:
            return twse[url] if params["startDate"].startswith("2024") else {"stat": "很抱歉，沒有符合條件的資料!"}

        def raw(url: str) -> bytes:
            if "revivt-2024" in url:
                return json.dumps({"fields": ["恢復買賣日期", "股票代號", "最後交易日之收盤價格", "減資恢復買賣開始日參考價格"],
                                   "data": [["1130205", "3064", "10.65", "35.50"]]}).encode()
            raise FileNotFoundError(url)

        out = module.refresh_official_events(2024, 2025, fetcher=fetcher, raw=raw, sleep=lambda s: None)
        self.assertEqual((out["twse"], out["tpex"]), (2, 1))
        self.assertEqual(len(out["errors"]), 1)          # 2025 櫃買鏡像沒有那一年
        self.assertEqual(sorted(price_adjust.load_events()), ["3064", "3432", "8476"])

        # 推測：0050 停止買賣 5 天後一拆四
        from official_daily_bars import _save_day

        days = [date(2025, 6, d) for d in (9, 10, 11, 12, 13, 16, 17, 18)]
        for d in days:
            prices = filler()
            if d <= date(2025, 6, 10):
                prices["0050"] = 188.65
            elif d == date(2025, 6, 18):
                prices["0050"] = 47.5
            _save_day(tse_rows(d, prices))
        self.assertEqual(module.infer_events(date(2025, 1, 1)), 1)
        self.assertEqual(price_adjust.load_events()["0050"], [("2025-06-18", 0.25)])
        self.assertEqual(module.infer_events(date(2025, 1, 1)), 0)   # 已經有事件：不重複


class RunTests(BaseCase):
    def test_run_once_flow(self) -> None:
        with patch.object(module, "backfill_tse", return_value={"inserted": 0}) as tse, \
             patch.object(module, "import_otc_from_mirror", return_value={"inserted": 5, "events": 0}) as otc, \
             patch.object(module, "refresh_official_events", return_value={"twse": 1, "tpex": 0, "errors": []}), \
             patch.object(module, "infer_events", return_value=0), \
             patch("heilong_backtest.rebuild", return_value={"date": "2026-10-02", "dates": 250, "rows": 10, "written": 10}) as rebuild:
            result = module.run_once()
        self.assertEqual(result["status"], "ok")
        tse.assert_called_once()
        otc.assert_called_once()
        rebuild.assert_called_once()                     # 上櫃有補進新的日K：黑龍表要重算
        state = module.status()
        self.assertEqual((state["running"], state["phase"], state["events"]["twse"]), (False, "done", 1))
        self.assertEqual(state["heilong"]["rows"], 10)

        with patch.object(module, "backfill_tse", return_value={"inserted": 0}), \
             patch.object(module, "import_otc_from_mirror", return_value={"inserted": 0, "events": 0}), \
             patch.object(module, "refresh_official_events", return_value={"twse": 0, "tpex": 0, "errors": []}), \
             patch.object(module, "infer_events", return_value=0), \
             patch("heilong_backtest.rebuild") as rebuild2:
            module.run_once()
        rebuild2.assert_not_called()                     # 什麼都沒變：不重算

    def test_target_start_and_coverage(self) -> None:
        self.assertEqual(module.target_start(date(2026, 10, 4), 3), date(2023, 10, 4))
        self.assertEqual(module.target_start(date(2028, 2, 29), 3), date(2025, 2, 28))
        cov = module.coverage()
        self.assertEqual((cov["bars"], cov["firstDate"]), (0, None))


if __name__ == "__main__":
    unittest.main()
