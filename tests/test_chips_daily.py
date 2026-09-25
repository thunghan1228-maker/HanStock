"""盤後籌碼第一步：證交所 T86 與櫃買中心開放資料的解析、收集（上市直抓、上櫃鏡像）、每日彙總端點。"""

from __future__ import annotations

import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch_history
import chips_daily as module
import database
import main_force_store
import persistent_app

TW = timezone(timedelta(hours=8))
T86_FIELDS = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)", "外陸資買賣超股數(不含外資自營商)",
              "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數", "投信買進股數", "投信賣出股數", "投信買賣超股數",
              "自營商買賣超股數", "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)",
              "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)", "三大法人買賣超股數"]


def t86(date_ymd, rows):
    return {"stat": "OK", "date": date_ymd, "fields": T86_FIELDS, "data": rows}


def t86_row(code, name, foreign_ex, foreign_dealer, trust, dealer, total):
    return [code, name, "0", "0", foreign_ex, "0", "0", foreign_dealer, "0", "0", trust, dealer, "0", "0", "0", "0", "0", "0", total]


def tpex_item(date_roc, code, name, foreign_all, trust, dealer, total):
    return {"Date": date_roc, "SecuritiesCompanyCode": code, "CompanyName": name,
            "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": str(foreign_all),
            "ForeignDealers-Difference": "0", "ForeignInvestorsInclude MainlandAreaInvestors-Difference": str(foreign_all),
            "SecuritiesInvestmentTrustCompanies-Difference": str(trust), "Dealers-Difference": str(dealer), "TotalDifference": str(total)}


class ParserTests(unittest.TestCase):
    def test_parse_twse_t86(self) -> None:
        day, rows = module.parse_twse_t86(t86("20260924", [t86_row("2330", "台積電  ", "1,000,000", "20,000", "-200,000", "50,000", "870,000")]))
        self.assertEqual(day, "2026-09-24")
        self.assertEqual(rows, [{"code": "2330", "name": "台積電", "foreign": 1020000, "trust": -200000, "dealer": 50000, "total": 870000}])
        self.assertEqual(module.parse_twse_t86({"stat": "很抱歉，沒有符合條件的資料!"}), (None, []))

    def test_parse_tpex_keeps_latest_day_and_roc_date(self) -> None:
        day, rows = module.parse_tpex_3insti([
            tpex_item("1150924", "6207", "雷科", -46913, 0, -2, -46915),
            tpex_item("1150923", "6207", "雷科", 100, 0, 0, 100),
            {"Date": "1150924", "SecuritiesCompanyCode": "3016", "CompanyName": "嘉晶",
             "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "5000", "ForeignDealers-Difference": "1000",
             "SecuritiesInvestmentTrustCompanies-Difference": "200", "Dealers-Difference": "-100"},   # 沒有合計欄：自己加
        ])
        self.assertEqual(day, "2026-09-24")
        self.assertEqual([r["code"] for r in rows], ["6207", "3016"])
        self.assertEqual(rows[0]["foreign"], -46913)
        self.assertEqual(rows[1], {"code": "3016", "name": "嘉晶", "date": "2026-09-24", "foreign": 6000, "trust": 200, "dealer": -100, "total": 6100})

    def test_streak_and_recent_trading_days(self) -> None:
        self.assertEqual(module._streak([5, 3, -1]), 2)
        self.assertEqual(module._streak([-2, -1, 0, 4]), -2)
        self.assertEqual(module._streak([0, 5]), 0)
        self.assertEqual(module._streak([None, 5]), 0)
        self.assertEqual(module._recent_trading_days(datetime(2026, 9, 24, 14, 0, tzinfo=TW), 2), ["2026-09-23", "2026-09-22"])   # 15:00 前不抓當天
        self.assertEqual(module._recent_trading_days(datetime(2026, 9, 24, 15, 30, tzinfo=TW), 2), ["2026-09-24", "2026-09-23"])
        self.assertEqual(module._recent_trading_days(datetime(2026, 9, 25, 10, 0, tzinfo=TW), 2), ["2026-09-24", "2026-09-23"])   # 中秋節


class CollectAndPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        main_force_store._table_ready_path = None
        main_force_store._ensure_table()
        self.codes_patch = patch.object(module, "industry_group_codes", return_value=frozenset({"2330", "6207", "3016"}))
        self.codes_patch.start()
        self.groups_patch = patch.object(brew_launch_history, "STOCK_GROUPS", {"半導體": [("2330", "台積電")], "設備股": [("6207", "雷科")], "矽晶圓": [("3016", "嘉晶")]})
        self.groups_patch.start()
        brew_launch_history._group_by_code.clear()
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", [
                ("2330", "2026-09-23T00:00:00", 1000, 1010, 990, 1000, 20000), ("2330", "2026-09-24T00:00:00", 1000, 1030, 995, 1020, 25000),
                ("6207", "2026-09-23T00:00:00", 114, 116, 113, 114, 800), ("6207", "2026-09-24T00:00:00", 115, 126, 114, 125, 1200),
            ])
            c.executemany("INSERT INTO main_force_bars (stock_code, trade_date, interval, bar_ts, main_buy_volume, main_sell_volume, main_net_volume, main_buy_amount, main_sell_amount, main_net_amount, main_tick_count, updated_at, total_amount) VALUES (?, ?, '5m', ?, ?, ?, ?, ?, ?, ?, 1, 'x', ?)", [
                ("2330", "2026-09-23", 1, 800, 500, 300, 8.0e8, 5.0e8, 3.0e8, 2.0e10),
                ("2330", "2026-09-24", 2, 900, 400, 500, 9.0e8, 4.0e8, 5.0e8, 2.5e10),
                ("2330", "2026-09-24", 3, 100, 50, 50, 1.0e8, 0.5e8, 0.5e8, 2.5e10),
                ("6207", "2026-09-24", 4, 100, 300, -200, 1.2e7, 3.7e7, -2.5e7, 1.5e8),
            ])

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        self.groups_patch.stop()
        self.codes_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        main_force_store._table_ready_path = None

    def _fetcher(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if "twse.com.tw" in url:
                if "date=20260924" in url:
                    return t86("20260924", [t86_row("2330", "台積電", "1,000,000", "20,000", "-200,000", "50,000", "870,000")])
                if "date=20260923" in url:
                    return t86("20260923", [t86_row("2330", "台積電", "300,000", "0", "100,000", "0", "400,000")])
                return {"stat": "很抱歉，沒有符合條件的資料!"}
            if url == module.TPEX_OPENAPI_URL:
                raise OSError("blocked")   # 正式站主機被櫃買中心擋
            if url.endswith("/3insti-latest.json"):
                return [tpex_item("1150924", "6207", "雷科", -46913, 0, -2, -46915), tpex_item("1150924", "3016", "嘉晶", 5000, 0, 0, 5000)]
            if url.endswith("/3insti-2026-09-23.json"):
                return [tpex_item("1150923", "6207", "雷科", -1000, 0, 0, -1000)]
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return fetch, calls

    def test_collect_then_payload(self) -> None:
        fetch, calls = self._fetcher()
        with patch.object(module, "BACKFILL_DAYS", 3):
            result = module.collect_once(now=datetime(2026, 9, 25, 10, 0, tzinfo=TW), fetcher=fetch, delay=0)
        self.assertEqual([(r["date"], r["rows"]) for r in result["twse"]], [("2026-09-24", 1), ("2026-09-23", 1), ("2026-09-22", 0)])
        self.assertEqual([(r["date"], r["rows"], r["source"]) for r in result["tpex"]], [("2026-09-24", 2, "mirror"), ("2026-09-23", 1, "mirror")])
        self.assertEqual(result["errors"], [])
        self.assertTrue(any(url == module.TPEX_OPENAPI_URL for url in calls))   # 先試直接抓
        # 再跑一次：都存過了，不會重抓證交所
        n = len(calls)
        with patch.object(module, "BACKFILL_DAYS", 2):
            again = module.collect_once(now=datetime(2026, 9, 25, 10, 0, tzinfo=TW), fetcher=fetch, delay=0)
        self.assertEqual(again["twse"], [])
        self.assertFalse(any("twse.com.tw" in url for url in calls[n:]))

        data = module.chips_daily()
        self.assertEqual(data["date"], "2026-09-24")
        self.assertEqual(data["dates"][:2], ["2026-09-24", "2026-09-23"])
        self.assertEqual(data["sources"], {"TSE": {"source": "twse", "rows": 1, "updatedAt": data["sources"]["TSE"]["updatedAt"]},
                                           "OTC": {"source": "mirror", "rows": 2, "updatedAt": data["sources"]["OTC"]["updatedAt"]}})
        tsmc = data["stocks"]["2330"]
        self.assertEqual((tsmc["foreign"], tsmc["trust"], tsmc["dealer"], tsmc["total"]), (1020.0, -200.0, 50.0, 870.0))   # 張
        self.assertEqual(tsmc["market"], "TSE")
        self.assertEqual(tsmc["group"], "半導體")
        self.assertEqual(tsmc["streak"], {"foreign": 2, "trust": -1, "dealer": 1, "total": 2, "mf": 2})
        self.assertEqual(tsmc["mf"]["net"], 550)
        self.assertEqual(tsmc["mf"]["netAmount"], 550_000_000)
        self.assertAlmostEqual(tsmc["mf"]["pct"], 2.2)
        self.assertEqual((tsmc["close"], tsmc["prevClose"], tsmc["changePct"], tsmc["volume"]), (1020.0, 1000.0, 2.0, 25000))
        lk = data["stocks"]["6207"]
        self.assertEqual(lk["market"], "OTC")
        self.assertEqual(lk["foreign"], -46.9)
        self.assertEqual(lk["streak"]["foreign"], -2)
        self.assertEqual(lk["mf"]["net"], -200)
        self.assertEqual(lk["streak"]["mf"], -1)
        self.assertEqual(data["stocks"]["3016"]["mf"], None)   # 那天沒有主力資料
        self.assertEqual(data["stocks"]["3016"]["close"], None)
        # 指定日期
        yesterday = module.chips_daily("2026-09-23")
        self.assertEqual(yesterday["stocks"]["2330"]["foreign"], 300.0)
        self.assertEqual(yesterday["stocks"]["2330"]["streak"]["foreign"], 1)

    def test_endpoints(self) -> None:
        client = TestClient(persistent_app.app)
        data = client.get("/api/hub/chips/daily").json()   # 還沒有法人資料：用最新有主力資料的那天
        self.assertEqual((data["status"], data["date"]), ("ok", "2026-09-24"))
        self.assertIsNone(data["stocks"]["2330"]["foreign"])
        self.assertEqual(data["stocks"]["2330"]["mf"]["net"], 550)
        self.assertEqual(client.get("/api/hub/chips/daily?date=bad").status_code, 422)
        status = client.get("/api/hub/chips/status").json()
        self.assertIn("enabled", status)
        self.assertIn("mirrorBase", status)


if __name__ == "__main__":
    unittest.main()
