"""現股當沖排行：上市證交所 TWTB4U、上櫃櫃買中心（直接抓不到改鏡像），照當沖張數排、當沖占比＝當沖股數 ÷ 那天成交股數。"""

from __future__ import annotations

import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import daytrade_rank as module
import database

TW = timezone(timedelta(hours=8))
FIELDS = ["證券代號", "證券名稱", "暫停現股賣出後\n現款買進當沖註記", "當日沖銷交易成交股數", "當日沖銷交易買進成交金額", "當日沖銷交易賣出成交金額"]


def payload(ymd: str, rows: list[list[str]], fields: list[str] = FIELDS) -> dict:
    return {"stat": "OK", "date": ymd, "tables": [{"title": "統計", "fields": ["當日沖銷交易總成交股數"], "data": [["1"]]},
                                                  {"title": "標的", "fields": fields, "data": rows}]}


TWSE_1118 = payload("20261118", [["2409", "友達", "", "170,705,000", "4,300,000,000", "4,310,000,000"],
                                 ["1802", "台玻", "", "142,634,000", "8,400,000,000", "8,410,000,000"],
                                 ["00632R", "元大台灣50反1", "", "900,000,000", "1", "1"],
                                 ["2327", "國巨", "*", "34,965,000", "1", "1"]])
TPEX_1118 = payload("20261118", [["6182", "合晶", "", "20,000,000", "1", "1"]])


class DaytradeRankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        with database.get_connection() as connection:
            bars = [("2409", "2026-11-17", 27.40, 200000), ("2409", "2026-11-18", 25.10, 232758),
                    ("1802", "2026-11-17", 65.20, 150000), ("1802", "2026-11-18", 58.70, 194537)]
            connection.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, 1, 1, 1, ?, ?)",
                                   [(c, f"{d}T00:00:00+00:00", px, v) for c, d, px, v in bars])

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_parse_handles_newlines_and_unpublished(self) -> None:
        day, rows = module.parse_daytrade(TWSE_1118)
        self.assertEqual(day, "2026-11-18")
        self.assertEqual(rows[0], {"code": "2409", "name": "友達", "shares": 170705000, "buy": 4300000000, "sell": 4310000000, "suspend": ""})
        self.assertEqual(rows[3]["suspend"], "*")
        # 還沒公布：只有標的清單（3 欄），沒有成交股數
        self.assertEqual(module.parse_daytrade(payload("20261119", [["2409", "友達", ""]], FIELDS[:3])), ("2026-11-19", []))

    def test_collect_twse_direct_and_tpex_mirror(self) -> None:
        urls = []

        def fetcher(url: str):
            urls.append(url)
            if "twse.com.tw" in url:
                return TWSE_1118 if "20261118" in url else payload(url.split("date=")[1][:8], [["2409", "友達", ""]], FIELDS[:3])
            if "tpex.org.tw" in url:
                raise OSError("blocked")
            if "daytrade-2026-11-18.json" in url:
                return TPEX_1118
            raise urllib.error.HTTPError(url, 404, "not found", None, None)

        now = datetime(2026, 11, 19, 17, 30, tzinfo=TW)
        with patch.object(module, "BACKFILL_DAYS", 2):
            result = module.collect_once(now, fetcher, delay=0)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["twse"], [{"date": "2026-11-19", "rows": 0, "note": "尚未公布"}, {"date": "2026-11-18", "rows": 4}])
        self.assertEqual(result["tpex"], [{"date": "2026-11-18", "rows": 1, "source": "mirror"}])
        self.assertTrue(module.has_market("2026-11-18", "OTC"))
        # 已經存的日子下一輪不再抓
        urls.clear()
        with patch.object(module, "BACKFILL_DAYS", 2):
            module.collect_once(now, fetcher, delay=0)
        self.assertFalse(any("20261118" in u or "2026/11/18" in u for u in urls))

    def test_before_publish_minute_skips_today(self) -> None:
        self.assertEqual(module._recent_trading_days(datetime(2026, 11, 19, 15, 0, tzinfo=TW), 2), ["2026-11-18", "2026-11-17"])
        self.assertEqual(module._recent_trading_days(datetime(2026, 11, 19, 16, 5, tzinfo=TW), 1), ["2026-11-19"])

    def test_rank_sorts_by_lots_with_ratio_and_skips_etf(self) -> None:
        module.save_daytrade("2026-11-18", "TSE", module.parse_daytrade(TWSE_1118)[1], "twse")
        payload_ = module.daytrade_rank(20)
        self.assertEqual(payload_["date"], "2026-11-18")
        self.assertTrue(payload_["partial"])                         # 上櫃還沒進來
        self.assertEqual([r["code"] for r in payload_["rows"]], ["2409", "1802", "2327"])   # ETF 不算
        top = payload_["rows"][0]
        self.assertEqual(top["dtLots"], 170705)
        self.assertEqual(top["ratio"], 73.34)                        # 170,705 張 ÷ 232,758 張（券商 App 寫 73.34）
        self.assertEqual(top["chg"], -8.39)
        self.assertIsNone(payload_["rows"][2]["ratio"])              # 沒有日K就不算占比
        self.assertEqual(payload_["rows"][2]["suspend"], "*")
        module.save_daytrade("2026-11-18", "OTC", module.parse_daytrade(TPEX_1118)[1], "mirror")
        self.assertFalse(module.daytrade_rank(2)["partial"])
        self.assertEqual(len(module.daytrade_rank(2)["rows"]), 2)

    def test_empty(self) -> None:
        self.assertEqual(module.daytrade_rank(20)["status"], "empty")


if __name__ == "__main__":
    unittest.main()
