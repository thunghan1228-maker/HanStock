"""大盤融資維持率：證交所融資融券彙總（MI_MARGN）每檔融資今日餘額 × 收盤價（MI_INDEX）加總 ÷ 融資金額。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
import margin_maintenance as module

TW = timezone(timedelta(hours=8))


def margn(ymd: str, amount_k: str, rows: list[tuple[str, str]]) -> dict:
    return {"stat": "OK", "date": ymd, "tables": [
        {"title": "信用交易統計", "fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
         "data": [["融資(交易單位)", "1", "1", "1", "1", "1"], ["融券(交易單位)", "1", "1", "1", "1", "1"], ["融資金額(仟元)", "1", "1", "1", "1", amount_k]]},
        {"title": "融資融券彙總 (全部)", "fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
                                              "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"],
         "data": [[code, code, "0", "0", "0", "0", lots, "0", "0", "0", "0", "0", "999", "0", "0", ""] for code, lots in rows]},
    ]}


def quotes(rows: list[tuple[str, str]]) -> dict:
    return {"stat": "OK", "tables": [{"title": "每日收盤行情", "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
                                      "data": [[code, code, "1", "1", "1", "1", "1", "1", close] for code, close in rows]}]}


class MarginMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def fetcher(self, days: dict[str, tuple[dict, dict]]):
        def call(url: str):
            ymd = url.split("date=")[1][:8]
            if ymd not in days:
                return {"stat": "很抱歉，沒有符合條件的資料!"}
            return days[ymd][0] if "MI_MARGN" in url else days[ymd][1]
        return call

    def test_ratio_is_market_value_over_margin_amount(self) -> None:
        # 2330：2,000 張 × 500 元 = 10 億；2409：10,000 張 × 25 元 = 2.5 億；9999 沒收盤價（停牌）不算市值
        day = (margn("20261120", "900,000", [("2330", "2,000"), ("2409", "10,000"), ("9999", "50")]), quotes([("2330", "500.00"), ("2409", "25.00")]))
        row = module.compute("2026-11-20", self.fetcher({"20261120": day}), delay=0)
        self.assertEqual(row["ratio"], 138.89)                 # 12.5 億 ÷ 9 億
        self.assertEqual(row["marginLots"], 12050)
        self.assertEqual((row["priced"], row["unpriced"]), (2, 1))
        self.assertIsNone(module.compute("2026-11-21", self.fetcher({"20261120": day}), delay=0))   # 還沒公布

    def test_collect_backfills_and_payload_shows_change(self) -> None:
        days = {
            "20261119": (margn("20261119", "1,000,000", [("2330", "3,000")]), quotes([("2330", "500.00")])),   # 150%
            "20261120": (margn("20261120", "1,000,000", [("2330", "2,800")]), quotes([("2330", "500.00")])),   # 140%
        }
        now = datetime(2026, 11, 20, 21, 30, tzinfo=TW)
        with patch.object(module, "BACKFILL_DAYS", 3):
            result = module.collect_once(now, self.fetcher(days), delay=0)
        self.assertEqual([a["date"] for a in result["added"]], ["2026-11-20", "2026-11-19"])
        self.assertEqual(result["pending"], ["2026-11-18"])
        p = module.payload(10)
        self.assertEqual(p["latest"]["date"], "2026-11-20")
        self.assertEqual(p["latest"]["ratio"], 140.0)
        self.assertEqual(p["latest"]["change"], -10.0)
        self.assertEqual([s["date"] for s in p["series"]], ["2026-11-20", "2026-11-19"])
        self.assertEqual(p["series"][0]["marginAmount"], 10.0)  # 億

    def test_before_publish_time_skips_today(self) -> None:
        self.assertEqual(module._recent_trading_days(datetime(2026, 11, 20, 20, 0, tzinfo=TW), 1), ["2026-11-19"])
        self.assertEqual(module._recent_trading_days(datetime(2026, 11, 20, 21, 5, tzinfo=TW), 1), ["2026-11-20"])

    def test_empty(self) -> None:
        self.assertEqual(module.payload()["status"], "empty")


if __name__ == "__main__":
    unittest.main()
