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
            if "/3insti-latest.json" in url:
                return [tpex_item("1150924", "6207", "雷科", -46913, 0, -2, -46915), tpex_item("1150924", "3016", "嘉晶", 5000, 0, 0, 5000)]
            if "/index.json?v=" in url:   # 會變的檔案要帶時間參數避開快取
                return ["2026-09-24", "2026-09-23"]
            if url.endswith("/3insti-2026-09-23.json"):   # 每天一份的檔案不帶參數
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
        self.assertFalse(any(url.endswith("/3insti-2026-09-22.json") for url in calls))   # 鏡像清單沒有的日子不去問
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
        self.assertIsNone(tsmc["score"])   # 黑龍表還沒整理那天：均線分數空著
        # 2026-10-13 使用者：放空籌碼的均線分數欄都是「—」——每檔附上那天收盤的均線分數（黑龍表 score2）與是否站上月線
        import heilong_backtest
        with database.get_connection() as connection:
            heilong_backtest._schema(connection)
            insert = ("INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, score2, ma_bits) "
                      "VALUES (?, ?, 1, 1, 1, 1, 1, ?, ?)")
            connection.executemany(insert, [("2026-09-24", "2330", 13, 0b111111), ("2026-09-24", "6207", 3, 0b000011),
                                            ("2026-09-23", "2330", 9, 0b000111)])
        data = module.chips_daily()
        self.assertEqual((data["stocks"]["2330"]["score"], data["stocks"]["2330"]["aboveMa20"]), (13, True))
        self.assertEqual((data["stocks"]["6207"]["score"], data["stocks"]["6207"]["aboveMa20"]), (3, False))   # 5、10 日線上，月線下
        self.assertIsNone(data["stocks"]["3016"]["score"])
        # 指定日期
        yesterday = module.chips_daily("2026-09-23")
        self.assertEqual(yesterday["stocks"]["2330"]["score"], 9)   # 往日也有那天的均線分數
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


class InstRankTests(unittest.TestCase):
    """研究室「外資／投信買賣超」：全市場一般股票、上市上櫃分開、往回查日期、連買連賣與近 5 日累計。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.codes_patch = patch.object(module, "industry_group_codes", return_value=frozenset({"2330"}))
        self.codes_patch.start()
        self.groups_patch = patch.object(brew_launch_history, "STOCK_GROUPS", {"半導體": [("2330", "台積電")]})
        self.groups_patch.start()
        brew_launch_history._group_by_code.clear()
        module._rank_cache.clear()

        def row(code, name, foreign, trust):
            return {"code": code, "name": name, "foreign": foreign * 1000, "trust": trust * 1000, "dealer": 0, "total": (foreign + trust) * 1000}

        # 三個交易日：上市 2330（外資連買）、2603（外資賣、投信買）、0050（ETF 不算）、2881A（特別股不算）；上櫃 6207（外資投信同賣）、3016
        days = {
            "2026-09-22": ([row("2330", "台積電", 1000, 0), row("2603", "長榮", -100, 5), row("0050", "元大台灣50", 5000, 0)], [row("6207", "雷科", -50, -20)]),
            "2026-09-23": ([row("2330", "台積電", 2000, 10), row("2603", "長榮", -300, 20), row("2881A", "富邦特", 99, 99)], [row("6207", "雷科", -80, -30), row("3016", "嘉晶", 400, 0)]),
            "2026-09-24": ([row("2330", "台積電", 3000, 50), row("2603", "長榮", -500, 40), row("1101", "台泥", 0, 0), row("0050", "元大台灣50", 9000, 0)], []),   # 9/24 上櫃還沒進來
        }
        for day, (tse, otc) in days.items():
            module.save_institutional(day, "TSE", tse, "twse")
            if otc:
                module.save_institutional(day, "OTC", otc, "mirror")
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", [
                ("2330", "2026-09-23T00:00:00", 1000, 1010, 990, 1000, 20000), ("2330", "2026-09-24T00:00:00", 1000, 1030, 995, 1020, 25000),
                ("2603", "2026-09-23T00:00:00", 200, 205, 198, 200, 9000), ("2603", "2026-09-24T00:00:00", 200, 201, 190, 192, 12000),
            ])

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        module._rank_cache.clear()
        self.groups_patch.stop()
        self.codes_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_latest_day_rows_and_filters(self) -> None:
        data = module.inst_rank()
        self.assertEqual((data["status"], data["date"], data["markets"]), ("ok", "2026-09-24", ["TSE"]))   # 最新一天只有上市
        self.assertEqual([d["date"] for d in data["dates"]], ["2026-09-24", "2026-09-23", "2026-09-22"])
        self.assertEqual(data["dates"][0]["markets"], ["TSE"])
        self.assertEqual(sorted(data["dates"][1]["markets"]), ["OTC", "TSE"])
        by = {r["code"]: r for r in data["rows"]}
        self.assertEqual(sorted(by), ["2330", "2603"])          # ETF、特別股、買賣超 0 的 1101 都不在
        tsmc = by["2330"]
        self.assertEqual((tsmc["f"], tsmc["t"], tsmc["mkt"], tsmc["g"]), (3000, 50, "TSE", "半導體"))
        self.assertEqual((tsmc["fs"], tsmc["ts"]), (3, 2))        # 外資連買 3 天；投信 9/22 是 0 所以連買 2 天
        self.assertEqual((tsmc["f5"], tsmc["t5"]), (6000, 60))
        self.assertEqual((tsmc["c"], tsmc["p"], tsmc["v"]), (1020.0, 2.0, 25000))
        ship = by["2603"]
        self.assertEqual((ship["f"], ship["t"], ship["fs"], ship["ts"], ship["g"]), (-500, 40, -3, 3, ""))   # 外資連賣、投信連買、不在 67 族群

    def test_past_date_has_otc_and_endpoint(self) -> None:
        data = module.inst_rank("2026-09-23")
        self.assertEqual(sorted(data["markets"]), ["OTC", "TSE"])
        self.assertEqual(data["counts"], {"TSE": 2, "OTC": 2})
        by = {r["code"]: r for r in data["rows"]}
        self.assertEqual((by["6207"]["f"], by["6207"]["t"], by["6207"]["mkt"], by["6207"]["fs"], by["6207"]["ts"]), (-80, -30, "OTC", -2, -2))
        self.assertEqual((by["3016"]["f"], by["3016"]["t"]), (400, 0))
        self.assertEqual((by["2330"]["fs"], by["2330"]["ts"], by["2330"]["f5"]), (2, 1, 3000))   # 回到 9/23：只算到那天為止
        client = TestClient(persistent_app.app)
        body = client.get("/api/hub/inst-rank?date=2026-09-22").json()
        self.assertEqual((body["status"], body["date"], len(body["rows"])), ("ok", "2026-09-22", 3))
        self.assertEqual(client.get("/api/hub/inst-rank?date=bad").status_code, 422)
        self.assertEqual(client.get("/api/hub/inst-rank?date=2026-01-01").json()["status"], "empty")

    def test_no_data(self) -> None:
        with database.get_connection() as c:
            c.execute("DELETE FROM institutional_daily")
        module._rank_cache.clear()
        self.assertEqual(module.inst_rank()["status"], "empty")


if __name__ == "__main__":
    unittest.main()
