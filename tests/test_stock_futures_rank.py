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


class StockFuturesRankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._contracts.update({"at": 0.0, "map": None})

    def tearDown(self) -> None:
        module._contracts.update({"at": 0.0, "map": None})
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
        self.assertEqual(result, {"date": "2026-11-20", "saved": 3})
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
