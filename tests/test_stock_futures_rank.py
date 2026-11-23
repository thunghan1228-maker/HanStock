"""股票期貨成交量排行：期交所每日行情＋標的清單，契約為單位（一般、小型分開），全部月份、盤後＋一般加總。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import persistent_app
import stock_futures_rank as module

TW = timezone(timedelta(hours=8))


def lists_page() -> str:
    def row(two, code, name, shares):
        return (f"<tr><td>{two}</td><td>某某股份有限公司</td><td>{code}</td><td>{name}</td><td>● 是股票期貨標的</td><td></td><td></td>"
                f"<td>◎ 是上市普通股標的證券</td><td></td><td></td><td></td><td>{shares}</td><td>8:45~13:45</td></tr>")
    rows = [row("CD", "2330", "台積電", "2,000"), row("QF", "2330", "台積電", "100"), row("SR", "0050", "元大台灣50ETF", "1,000"),
            row("CC", "2303", "聯電", "2,000"),
            "<tr><td>ZZ</td><td>不是股期</td><td>9999</td><td>別的</td><td>● 是股票選擇權標的</td><td></td><td></td><td></td><td></td><td></td><td></td><td>2,000</td></tr>"]
    filler = [f"{a}{b}" for a in "XYW" for b in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"][:100]
    rows += [row(two, str(5000 + i), f"填充{i}", "2,000") for i, two in enumerate(filler)]
    return "<table>" + "".join(rows) + "</table>"


def fut(contract, month, volume, oi="-", session="一般", last="1997", pct="-5.40%", date="20261120"):
    return {"Date": date, "Contract": contract, "ContractMonth(Week)": month, "Last": last, "%": pct, "Volume": str(volume),
            "OpenInterest": str(oi), "TradingSession": session}


DAILY = [
    fut("CDF", "202612", 15982, 25025), fut("CDF", "202701", 558, 313, last="2007", pct="-5.24%"),
    fut("CDF", "202612", 4323, session="盤後", last="2091", pct="-0.95%"), fut("CDF", "202612/202701", 285, last="8.2", pct="-"),
    fut("QFF", "202612", 68186, 71454), fut("QFF", "202612", 26611, session="盤後", last="2091", pct="-0.95%"),
    fut("SRF", "202612", 100, 50, last="70.5", pct="-3.1%"),
    fut("CCF", "202612", 0, 0),                       # 沒成交、沒未平倉：不列
    fut("TX", "202612", 100000, 80000),               # 指數期貨：不是股期
]


LARGE_CSV = "\n".join([
    "日期,商品(契約),商品名稱(契約名稱),到期月份(週別),交易人類別,前五大交易人買方,前五大交易人賣方,前十大交易人買方,前十大交易人賣方,全市場未沖銷部位數",
    "2026/11/19,CD     ,台積電期貨,202612  ,0,1,1,1,1,1",                     # 近月那列不算，只看全部月份
    "2026/11/19,CD     ,台積電期貨,999999  ,0,15584,10108,17237,16383,27830",
    "2026/11/19,CD     ,台積電期貨,999999  ,1,15584,10108,16428,16383,27830",
    "2026/11/19,QF     ,小型台積電期貨,999999  ,0,6237,66764,7865,72358,80961",
    "2026/11/19,SR     ,小型元大台灣50ETF期貨,999999  ,0,10,10,500,100,900",
    "2026/11/19,TX     ,臺股期貨,999999  ,0,1,1,1,1,1",                       # 不是股期
    "2026/11/20,CD     ,台積電期貨,999999  ,0,16076,9872,17549,16041,26559",
    "2026/11/20,CD     ,台積電期貨,999999  ,1,16076,9872,16843,16041,26559",
    "2026/11/20,QF     ,小型台積電期貨,999999  ,0,10313,68674,12039,74966,82652",
    "2026/11/20,SR     ,小型元大台灣50ETF期貨,999999  ,0,10,10,400,300,900",
    "2026/11/20,CC     ,聯電期貨,999999  ,0,1,1,5,5,10",                      # 前一天沒有：不算
    "交易人類別格式： 0 為部位排序前五大或前十大交易人",
])


class StockFuturesRankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._contracts.update({"at": 0.0, "map": None})
        self.csv_patch = patch.object(module, "_fetch_csv", side_effect=OSError("測試不連網"))
        self.csv_patch.start()

    def tearDown(self) -> None:
        module._contracts.update({"at": 0.0, "map": None})
        self.csv_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_parse_contracts(self) -> None:
        mapping = module.parse_contracts(lists_page())
        self.assertEqual(mapping["CDF"], {"code": "2330", "name": "台積電", "shares": 2000})
        self.assertEqual(mapping["QFF"]["shares"], 100)
        self.assertNotIn("ZZF", mapping)                # 只有選擇權標的
        self.assertEqual(len(mapping), 104)

    def test_parse_daily_sums_months_and_sessions(self) -> None:
        day, rows = module.parse_daily(DAILY, module.parse_contracts(lists_page()))
        self.assertEqual(day, "2026-11-20")
        by = {r["contract"]: r for r in rows}
        self.assertEqual(sorted(by), ["CDF", "QFF", "SRF"])
        cdf = by["CDF"]
        self.assertEqual((cdf["volume"], cdf["night"], cdf["oi"]), (20863, 4323, 25338))   # 價差那列不算
        self.assertEqual(cdf["near"], {"month": "202612", "last": 1997.0, "pct": -5.4})
        self.assertEqual(module.parse_daily({"error": 1}, {}), (None, []))

    def test_collect_and_payload_with_previous_day(self) -> None:
        texts = []
        result = module.collect_once(fetcher=lambda url: DAILY, text_fetcher=lambda url: texts.append(url) or lists_page())
        self.assertEqual((result["date"], result["saved"]), ("2026-11-20", 3))
        module.collect_once(fetcher=lambda url: DAILY, text_fetcher=lambda url: texts.append(url) or lists_page())
        self.assertEqual(len(texts), 1)                 # 標的清單半天抓一次
        module.save("2026-11-19", [{"contract": "CDF", "code": "2330", "name": "台積電", "shares": 2000, "volume": 30000, "night": 0, "oi": 1, "near": None},
                                   {"contract": "QFF", "code": "2330", "name": "台積電", "shares": 100, "volume": 10000, "night": 0, "oi": 1, "near": None}])
        data = module.payload(top=30)
        self.assertEqual((data["status"], data["date"], data["prevDate"], data["contracts"], data["totalVolume"]), ("ok", "2026-11-20", "2026-11-19", 3, 94797 + 20863 + 100))
        self.assertEqual([(r["contract"], r["kind"]) for r in data["rows"]], [("QFF", "mini"), ("CDF", "std"), ("SRF", "etf")])
        top = data["rows"][0]
        self.assertEqual((top["volume"], top["night"], top["oi"], top["month"], top["last"], top["chg"]), (94797, 26611, 71454, "202612", 1997.0, -5.4))
        self.assertEqual((top["prevVolume"], top["prevRank"], data["rows"][1]["prevRank"], data["rows"][2]["prevRank"]), (10000, 2, 1, None))
        self.assertEqual(len(module.payload(top=1)["rows"]), 1)

    def test_parse_large(self) -> None:
        by_day = module.parse_large(LARGE_CSV, module.parse_contracts(lists_page()))
        self.assertEqual(sorted(by_day), ["2026-11-19", "2026-11-20"])
        cd = next(r for r in by_day["2026-11-20"] if r["contract"] == "CDF")
        self.assertEqual((cd["buy10"], cd["sell10"], cd["instBuy10"], cd["instSell10"], cd["oi"]), (17549, 16041, 16843, 16041, 26559))
        self.assertEqual(sorted(r["contract"] for r in by_day["2026-11-19"]), ["CDF", "QFF", "SRF"])

    def test_buy_sell_flows(self) -> None:
        urls = []
        result = module.collect_once(fetcher=lambda url: DAILY, text_fetcher=lambda url: lists_page(),
                                     csv_fetcher=lambda url: urls.append(url) or LARGE_CSV, now=datetime(2026, 11, 22, 15, 0, tzinfo=TW))
        self.assertEqual(result["large"]["saved"], {"2026-11-19": 3, "2026-11-20": 4})
        self.assertIn("queryStartDate=2026%2F11%2F08&queryEndDate=2026%2F11%2F22", urls[0])
        data = module.payload()
        self.assertEqual((data["flowDate"], data["flowPrevDate"]), ("2026-11-20", "2026-11-19"))
        # 台積電：淨部位 854 → 1,508，+654 口 × 2,000 股 × 1,997 元＝26.12 億；小型台積電 −64,493 → −62,927，+1,566 口 × 100 股＝3.13 億
        self.assertEqual([(r["contract"], r["change"], r["amount"], r["rank"]) for r in data["buy"]], [("CDF", 654, 26.12, 1), ("QFF", 1566, 3.13, 2)])
        tsmc = data["buy"][0]
        self.assertEqual((tsmc["lots"], tsmc["net"], tsmc["prevNet"], tsmc["instNet"], tsmc["kind"]), (1308.0, 1508, 854, 802, "std"))
        self.assertEqual([(r["contract"], r["change"], r["kind"]) for r in data["sell"]], [("SRF", -300, "etf")])   # 400 → 100
        again = module.collect_once(fetcher=lambda url: DAILY, text_fetcher=lambda url: lists_page(), csv_fetcher=lambda url: LARGE_CSV,
                                    now=datetime(2026, 11, 22, 15, 20, tzinfo=TW))
        self.assertEqual(again["large"]["saved"], {"2026-11-20": 4})         # 舊的日子存過就不再寫，最新一天照樣更新

    def test_large_download_failure_keeps_daily(self) -> None:
        def broken(url):
            raise OSError("blocked")
        result = module.collect_once(fetcher=lambda url: DAILY, text_fetcher=lambda url: lists_page(), csv_fetcher=broken)
        self.assertEqual(result["saved"], 3)
        self.assertIn("OSError", result["large"]["error"])
        data = module.payload()
        self.assertEqual((data["status"], data["buy"], data["sell"], data["flowPrevDate"]), ("ok", [], [], None))

    def test_parse_live_takes_near_month(self) -> None:
        rows = {"RtData": {"QuoteList": [
            {"SymbolID": "CDF-S", "CLastPrice": "2000"},                                                      # 現貨那列不算
            {"SymbolID": "CDFA7-F", "CLastPrice": "1990", "CDiffRate": "-0.5", "CTime": "090000"},             # 2027/01
            {"SymbolID": "CDFL6-F", "CLastPrice": "1981.00", "CRefPrice": "1996", "CDiff": "-15.00", "CDiffRate": "-0.75", "CTotalVolume": "1417", "CTime": "090133"},
            {"SymbolID": "QFFL6-F", "CLastPrice": "", "CRefPrice": "1996", "CDiffRate": "0.00", "CTotalVolume": "", "CTime": ""},   # 還沒成交
            {"SymbolID": "ZZFA0-F", "CLastPrice": "10", "CTime": "090000"}, {"SymbolID": "ZZFL9-F", "CLastPrice": "9", "CTime": "090000"},   # 跨十年
            {"SymbolID": "CDFL6/A7-F", "CLastPrice": "9"},                                                     # 價差不算
        ]}}
        got = module.parse_live(rows, now=datetime(2029, 11, 23, 9, 0, tzinfo=TW))
        self.assertEqual(got["CDF"], {"last": 1981.0, "ref": 1996.0, "diff": -15.0, "pct": -0.75, "volume": 1417, "time": "09:01:33", "symbol": "CDFL6-F"})
        self.assertEqual((got["QFF"]["last"], got["QFF"]["time"]), (None, None))
        self.assertEqual(got["ZZF"]["symbol"], "ZZFL9-F")                       # 2029/12 比 2030/01 近
        self.assertEqual(module.parse_live({"oops": 1}), {})

    def test_live_payload_merges_kinds_and_caches(self) -> None:
        module._live_cache.update({"at": 0.0, "data": None})
        calls = []
        def fetch(url, body):
            calls.append(body["KindID"])
            if body["KindID"] == "9":
                raise OSError("timeout")
            sym = {"4": "CDFL6-F", "5": "NYFL6-F", "8": "QFFL6-F"}[body["KindID"]]
            return {"RtData": {"QuoteList": [{"SymbolID": sym, "CLastPrice": "100", "CDiffRate": "1.5", "CTime": "091500"}]}}
        data = module.live_payload(fetch)
        self.assertEqual((data["status"], sorted(data["quotes"]), data["asOf"], data["errors"]), ("ok", ["CDF", "NYF", "QFF"], "09:15:00", ["9: OSError"]))
        self.assertIs(module.live_payload(fetch), data)                             # 15 秒內不重抓
        self.assertEqual(calls, ["4", "5", "8", "9"])
        module._live_cache.update({"at": 0.0, "data": None})
        def down(url, body):
            raise OSError("down")
        self.assertEqual(module.live_payload(down)["status"], "error")
        module._live_cache.update({"at": 0.0, "data": None})

    def test_due(self) -> None:
        fri = lambda h, m: datetime(2026, 11, 20, h, m, tzinfo=TW)   # noqa: E731
        self.assertTrue(module._due(fri(9, 0), None))
        self.assertFalse(module._due(fri(13, 0), "2026-11-19"))
        self.assertTrue(module._due(fri(14, 0), "2026-11-19"))
        self.assertFalse(module._due(fri(14, 0), "2026-11-20"))
        self.assertFalse(module._due(datetime(2026, 11, 21, 15, 0, tzinfo=TW), "2026-11-20"))   # 星期六

    def test_endpoint_and_empty(self) -> None:
        client = TestClient(persistent_app.app)
        self.assertEqual(client.get("/api/hub/stock-futures-rank").json()["status"], "empty")
        module.collect_once(fetcher=lambda url: DAILY, text_fetcher=lambda url: lists_page())
        body = client.get("/api/hub/stock-futures-rank?top=5").json()
        self.assertEqual([r["contract"] for r in body["rows"]], ["QFF", "CDF", "SRF"])
        self.assertEqual(client.get("/api/hub/stock-futures-rank?top=2").status_code, 422)


if __name__ == "__main__":
    unittest.main()
