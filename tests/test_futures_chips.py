"""外資空單：FinMind 期貨／選擇權三大法人、全市場未平倉、三大法人現貨解析，期交所 P/C 表格解析與分段重試，卡片與序列、排程、端點。
數字用老師 10/15 那頁對過的真實資料（外資台指 −97,582、增減 −10,768……）。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import futures_chips as module
import persistent_app

TW = module.TW_TZ
D1, D2 = "2026-10-14", "2026-10-15"


def inst(fid: str, rows: dict[str, dict[str, tuple[int, int]]]) -> dict:
    return {"data": [{"futures_id": fid, "date": d, "institutional_investors": who, "long_open_interest_balance_volume": lo,
                      "short_open_interest_balance_volume": sh} for d, by in rows.items() for who, (lo, sh) in by.items()]}


FINMIND = {
    ("TaiwanFuturesInstitutionalInvestors", "TX"): inst("TX", {
        D1: {"自營商": (5000, 2686), "投信": (80000, 1583), "外資": (20000, 106814)},
        D2: {"自營商": (9000, 2106), "投信": (80500, 1571), "外資": (18000, 115582)}}),
    ("TaiwanFuturesInstitutionalInvestors", "MTX"): inst("MTX", {
        D1: {"自營商": (484, 9379), "投信": (1, 15), "外資": (3000, 2810)},
        D2: {"自營商": (500, 11662), "投信": (0, 21), "外資": (2000, 5928)}}),
    ("TaiwanFuturesInstitutionalInvestors", "TMF"): inst("TMF", {
        D1: {"自營商": (11861, 45461), "投信": (0, 0), "外資": (1000, 5534)},
        D2: {"自營商": (10000, 85155), "投信": (0, 0), "外資": (1000, 13415)}}),
    ("TaiwanOptionInstitutionalInvestors", "TXO"): {"data": [
        {"date": D1, "call_put": "買權", "institutional_investors": "外資", "long_open_interest_balance_volume": 1000, "short_open_interest_balance_volume": 1355},
        {"date": D1, "call_put": "賣權", "institutional_investors": "外資", "long_open_interest_balance_volume": 3651, "short_open_interest_balance_volume": 2000},
        {"date": D1, "call_put": "買權", "institutional_investors": "自營商", "long_open_interest_balance_volume": 9, "short_open_interest_balance_volume": 1},
        {"date": D2, "call_put": "買權", "institutional_investors": "外資", "long_open_interest_balance_volume": 1000, "short_open_interest_balance_volume": 1169},
        {"date": D2, "call_put": "賣權", "institutional_investors": "外資", "long_open_interest_balance_volume": 7820, "short_open_interest_balance_volume": 2000}]},
    ("TaiwanFuturesDaily", "MTX"): {"data": [
        {"date": D1, "contract_date": "202610", "trading_session": "position", "open_interest": 30000},
        {"date": D1, "contract_date": "202611", "trading_session": "position", "open_interest": 9023},
        {"date": D1, "contract_date": "202610/202611", "trading_session": "position", "open_interest": 77},   # 價差單不算
        {"date": D1, "contract_date": "202610", "trading_session": "after_market", "open_interest": 999},     # 夜盤不算
        {"date": D2, "contract_date": "202610", "trading_session": "position", "open_interest": 44732}]},
    ("TaiwanFuturesDaily", "TMF"): {"data": [
        {"date": D1, "contract_date": "202610", "trading_session": "position", "open_interest": 108789},
        {"date": D2, "contract_date": "202610", "trading_session": "position", "open_interest": 139783}]},
    ("TaiwanStockPrice", "TAIEX"): {"data": [{"date": D1, "close": 48625.83}, {"date": D2, "close": 46969.87}]},
    ("TaiwanStockTotalInstitutionalInvestors", None): {"data": [
        {"date": D2, "name": "Foreign_Investor", "buy": 100e8, "sell": 3125.55e8},
        {"date": D2, "name": "Foreign_Dealer_Self", "buy": 0, "sell": 0},
        {"date": D2, "name": "Investment_Trust", "buy": 400e8, "sell": 116.97e8},
        {"date": D2, "name": "Dealer_self", "buy": 100e8, "sell": 186.83e8},
        {"date": D2, "name": "Dealer_Hedging", "buy": 100e8, "sell": 971.41e8},
        {"date": D2, "name": "total", "buy": 700e8, "sell": 4400.76e8}]},
}

PC_PAGE = """<table><tr><th>日期</th><th>賣權成交量</th><th>買權成交量</th><th>買賣權成交量比率%</th><th>賣權未平倉量</th><th>買權未平倉量</th><th>買賣權未平倉量比率%</th></tr>
<tr><td>2026/10/15</td><td>191,704</td><td>255,629</td><td>74.99</td><td>77,287</td><td>127,036</td><td>60.84</td></tr>
<tr><td>2026/10/14</td><td>337,868</td><td>326,539</td><td>103.47</td><td>50,695</td><td>68,105</td><td>74.44</td></tr></table>"""


def finmind(params: dict[str, str]) -> dict:
    return FINMIND[(params["dataset"], params.get("data_id"))]


class FuturesChipsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        self.pause_patch = patch.object(module, "PC_PAUSE", 0)
        self.pause_patch.start()
        database.initialize_database()
        module._state.update({"lastFetch": None, "lastError": None, "pcError": None})
        self.now = datetime(2026, 10, 15, 15, 45, tzinfo=TW)

    def tearDown(self) -> None:
        self.pause_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_parse(self) -> None:
        tx = module.parse_inst_net(FINMIND[("TaiwanFuturesInstitutionalInvestors", "TX")])
        self.assertEqual(tx["f"][D2], -97582)
        self.assertEqual(tx["t"][D2], 78929)
        self.assertEqual(module.parse_total_oi(FINMIND[("TaiwanFuturesDaily", "MTX")])[D1], 39023)   # 不含價差單、夜盤
        opt = module.parse_option_foreign(FINMIND[("TaiwanOptionInstitutionalInvestors", "TXO")])
        self.assertEqual((opt["call"][D2], opt["put"][D2]), (-169, 5820))
        spot = module.parse_spot(FINMIND[("TaiwanStockTotalInstitutionalInvestors", None)])
        self.assertAlmostEqual(spot["spot_f"][D2], -3025.55, places=2)
        self.assertAlmostEqual(spot["spot_d"][D2], -958.24, places=2)
        pc = module.parse_pc_ratio(PC_PAGE)
        self.assertEqual(pc["pc_oi"], {D2: 60.84, D1: 74.44})

    def test_fetch_and_payload_match_teacher(self) -> None:
        pages = []
        module.fetch(fetcher=finmind, pc_fetcher=lambda a, b: pages.append((a, b)) or PC_PAGE, now=self.now)
        self.assertTrue(len(pages) >= 14)                              # 第一次抓 400 天，分段每段不超過 30 天
        self.assertTrue(all((b - a).days <= 30 for a, b in pages))
        self.assertEqual(pages[-1][1], date(2026, 10, 15))
        body = module.payload(now=self.now)
        self.assertEqual(body["status"], "ok")
        self.assertEqual((body["asOf"], body["prevDate"]), (D2, D1))
        c = body["cards"]
        self.assertEqual(c["taiex"], {"value": 46969.87, "chg": -1655.96, "pct": -3.41})
        self.assertEqual(c["txForeign"], {"value": -97582, "prev": -86814, "chg": -10768})
        self.assertEqual((c["txTrust"]["value"], c["txDealer"]["value"]), (78929, 6894))
        self.assertEqual((c["callForeign"]["value"], c["callForeign"]["chg"]), (-169, 186))
        self.assertEqual((c["putForeign"]["value"], c["putForeign"]["chg"]), (5820, 4169))
        self.assertEqual(c["mtxRetail"], {"value": 15111, "prev": 8719, "chg": 6392})
        self.assertEqual(c["tmfRetail"], {"value": 87570, "prev": 38134, "chg": 49436})
        self.assertEqual((c["mtxRatio"]["value"], c["mtxRatio"]["prev"]), (33.78, 22.34))
        self.assertEqual((c["tmfRatio"]["value"], c["tmfRatio"]["prev"]), (62.65, 35.05))
        self.assertEqual((c["pcRatio"]["value"], c["pcRatio"]["prev"]), (60.84, 74.44))
        self.assertEqual(c["spot"]["total"], -3700.76)
        last = body["series"][-1]
        self.assertEqual((last["d"], last["f"], last["fc"], last["mr"], last["pc"]), (D2, -97582, -10768, 15111, 60.84))
        self.assertIsNone(body["series"][0]["fc"])                       # 第一天沒有前一天可以比

    def test_pc_retry_and_failure_kept_separate(self) -> None:
        calls = {"n": 0}

        def flaky(a: date, b: date) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("520")
            return PC_PAGE

        module._state["lastFetch"] = None
        with patch.object(module, "HISTORY_DAYS", 20):
            module.fetch(fetcher=finmind, pc_fetcher=flaky, now=self.now)
        self.assertEqual(calls["n"], 2)                                  # 失敗一次、重試成功
        self.assertIsNone(module.collector_status()["pcError"])
        with patch.object(module, "HISTORY_DAYS", 20):
            module.fetch(fetcher=finmind, pc_fetcher=lambda a, b: (_ for _ in ()).throw(OSError("down")), now=self.now)
        self.assertIn("down", module.collector_status()["pcError"])      # P/C 掛了不影響其他數字
        self.assertEqual(module.payload(now=self.now)["cards"]["txForeign"]["value"], -97582)

    def test_missing_and_due(self) -> None:
        self.assertEqual(module.payload(now=self.now)["status"], "missing")
        self.assertTrue(module.due(self.now, None))
        self.assertFalse(module.due(datetime(2026, 10, 15, 15, 30, tzinfo=TW), "2026-10-14T20:41:00+08:00"))
        self.assertTrue(module.due(datetime(2026, 10, 15, 15, 41, tzinfo=TW), "2026-10-14T20:41:00+08:00"))
        self.assertFalse(module.due(datetime(2026, 10, 15, 16, 0, tzinfo=TW), "2026-10-15T15:41:00+08:00"))
        self.assertTrue(module.due(datetime(2026, 10, 15, 17, 45, tzinfo=TW), "2026-10-15T15:41:00+08:00"))   # 17:40 補抓
        self.assertFalse(module.due(datetime(2026, 10, 17, 16, 0, tzinfo=TW), "2026-10-16T20:41:00+08:00"))  # 週六不抓

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        with patch.object(module, "_default_fetcher", finmind), patch.object(module, "_default_pc_fetcher", lambda a, b: PC_PAGE):
            r = client.get("/api/hub/futures-chips")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["cards"]["txForeign"]["chg"], -10768)


if __name__ == "__main__":
    unittest.main()
