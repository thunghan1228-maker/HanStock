"""基本面與集保週籌碼：解析、收集（假抓取器）、查詢。"""

from __future__ import annotations

import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import brew_launch
import database
import fundamentals_daily as module

TW = timezone(timedelta(hours=8))
GROUPS = {"矽晶圓": [("6182", "合晶")], "設備股": [("6207", "雷科")], "半導體": [("2330", "台積電")]}


def twse_pe(date_ymd, rows):
    return {"stat": "OK", "date": date_ymd, "fields": ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"], "data": rows}


class ParserTests(unittest.TestCase):
    def test_parse_twse_pe(self) -> None:
        day, rows = module.parse_twse_pe(twse_pe("20260924", [["2330", "台積電", "1020.00", "1.50", 114, "25.30", "6.10", "115/2"], ["1101", "台泥", "25.25", "3.17", 114, "-", "0.82", "115/2"]]))
        self.assertEqual(day, "2026-09-24")
        self.assertEqual(rows[0], {"code": "2330", "name": "台積電", "pe": 25.3, "pbr": 6.1, "yield": 1.5})
        self.assertIsNone(rows[1]["pe"])
        self.assertEqual(module.parse_twse_pe({"stat": "查無資料"}), (None, []))

    def test_parse_tpex_pe_revenue_basics_tdcc(self) -> None:
        day, rows = module.parse_tpex_pe([{"Date": "1150924", "SecuritiesCompanyCode": "6182", "CompanyName": "合晶", "PriceEarningRatio": "N/A", "YieldRatio": "0.00", "PriceBookRatio": "3.10"}])
        self.assertEqual((day, rows[0]["code"], rows[0]["pe"], rows[0]["pbr"]), ("2026-09-24", "6182", None, 3.1))
        rev = module.parse_revenue([{"資料年月": "11508", "公司代號": "6182", "營業收入-當月營收": "1,234,567", "營業收入-去年同月增減(%)": "20.5", "營業收入-上月比較增減(%)": "-1.2"}, {"資料年月": "bad", "公司代號": "x"}])
        self.assertEqual(rev, [{"code": "6182", "ym": "2026-08", "revenue": 1234567, "yoy": 20.5, "mom": -1.2}])
        self.assertEqual(module.parse_basics([{"公司代號": "2330", "已發行普通股數或TDR原股發行股數": "25930380458"}, {"公司代號": "9999"}]), {"2330": 25930380458})
        day, rows = module.parse_tdcc_mirror({"date": "2026-09-24", "rows": [["6182", 15, 30, 120000000, 25.5], ["6182", "17", "1000", "470000000", "100.00"], ["bad"]]})
        self.assertEqual(day, "2026-09-24")
        self.assertEqual(rows, [("6182", 15, 30, 120000000, 25.5), ("6182", 17, 1000, 470000000, 100.0)])

    def test_pe_candidate_dates(self) -> None:
        self.assertEqual(module._pe_candidate_dates(datetime(2026, 9, 24, 15, 0, tzinfo=TW), 2), ["2026-09-23", "2026-09-22"])   # 16:30 前不算今天
        self.assertEqual(module._pe_candidate_dates(datetime(2026, 9, 24, 17, 0, tzinfo=TW), 2), ["2026-09-24", "2026-09-23"])
        self.assertEqual(module._pe_candidate_dates(datetime(2026, 9, 26, 10, 0, tzinfo=TW), 1), ["2026-09-24"])   # 週六、中秋


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.groups_patch = patch.object(brew_launch, "STOCK_GROUPS", GROUPS)
        self.groups_patch.start()

    def tearDown(self) -> None:
        self.groups_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _fetcher(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if "BWIBBU_d" in url:
                if "date=20260924" in url:
                    return twse_pe("20260924", [["2330", "台積電", "1020.00", "1.50", 114, "25.30", "6.10", "115/2"], ["1101", "台泥", "25.25", "3.17", 114, "-", "0.82", "115/2"]])
                return {"stat": "查無資料"}
            if url.startswith(module.TWSE_REVENUE_URL):
                return [{"資料年月": "11508", "公司代號": "2330", "營業收入-當月營收": "300000000", "營業收入-去年同月增減(%)": "33.8", "營業收入-上月比較增減(%)": "2.0"}]
            if url.startswith(module.TWSE_BASICS_URL):
                return [{"公司代號": "2330", "已發行普通股數或TDR原股發行股數": "25930380458"}, {"公司代號": "1101", "已發行普通股數或TDR原股發行股數": "7523181742"}]
            if "/peratio-latest.json?v=" in url:
                return [{"Date": "1150924", "SecuritiesCompanyCode": "6182", "CompanyName": "合晶", "PriceEarningRatio": "62.00", "YieldRatio": "0.5", "PriceBookRatio": "3.10"},
                        {"Date": "1150924", "SecuritiesCompanyCode": "6207", "CompanyName": "雷科", "PriceEarningRatio": "N/A", "YieldRatio": "0", "PriceBookRatio": "2"}]
            if "/revenue-latest.json?v=" in url:
                return [{"資料年月": "11508", "公司代號": "6182", "營業收入-當月營收": "1000000", "營業收入-去年同月增減(%)": "20.0", "營業收入-上月比較增減(%)": "1.0"},
                        {"資料年月": "11507", "公司代號": "6182", "營業收入-當月營收": "900000", "營業收入-去年同月增減(%)": "10.0", "營業收入-上月比較增減(%)": "0.5"},
                        {"資料年月": "11508", "公司代號": "6207", "營業收入-當月營收": "500000", "營業收入-去年同月增減(%)": "-6.0", "營業收入-上月比較增減(%)": "0"}]
            if "/basics-latest.json?v=" in url:
                return [{"公司代號": "6182", "已發行普通股數或TDR原股發行股數": "470000000"}]
            if "/tdcc-index.json?v=" in url:
                return ["2026-09-24", "2026-09-19", "2026-09-12"]
            if url.endswith("/tdcc-2026-09-24.json"):
                return {"date": "2026-09-24", "rows": [["6182", 12, 20, 10000000, 2.1], ["6182", 15, 30, 120000000, 25.5], ["6182", 1, 5000, 3000000, 0.6], ["2330", 15, 100, 20000000000, 77.0]]}
            if url.endswith("/tdcc-2026-09-19.json"):
                return {"date": "2026-09-19", "rows": [["6182", 12, 19, 9000000, 1.9], ["6182", 15, 29, 111000000, 23.6], ["2330", 15, 100, 20100000000, 77.4]]}
            if url.endswith("/tdcc-2026-09-12.json"):
                return {"date": "2026-09-12", "rows": [["6182", 12, 19, 9500000, 2.0], ["6182", 15, 28, 100000000, 21.3]]}
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return fetch, calls

    def test_collect_then_query(self) -> None:
        fetch, calls = self._fetcher()
        result = module.collect_once(now=datetime(2026, 9, 26, 10, 0, tzinfo=TW), fetcher=fetch)
        self.assertEqual((result["peTSE"]["date"], result["peTSE"]["rows"]), ("2026-09-24", 1))   # 只留族群股票（台泥不算）
        self.assertEqual((result["peOTC"]["date"], result["peOTC"]["rows"]), ("2026-09-24", 2))
        self.assertEqual((result["revenueTSE"]["rows"], result["revenueOTC"]["rows"], result["revenueOTC"]["ym"]), (1, 3, "2026-08"))
        self.assertEqual((result["sharesTSE"]["rows"], result["sharesOTC"]["rows"]), (1, 1))
        self.assertEqual(result["tdcc"]["added"], ["2026-09-24", "2026-09-19", "2026-09-12"])
        pe = module.latest_pe(["2330", "6182", "6207"])
        self.assertEqual((pe["2330"]["pe"], pe["6182"]["pe"], pe["6207"]["pe"], pe["2330"]["date"]), (25.3, 62.0, None, "2026-09-24"))
        rev = module.latest_revenue(["2330", "6182", "6207"])
        self.assertEqual((rev["6182"]["ym"], rev["6182"]["yoy"], rev["6207"]["yoy"], rev["2330"]["yoy"]), ("2026-08", 20.0, -6.0, 33.8))
        self.assertEqual(module.shares_map(["2330", "6182"]), {"2330": 25930380458, "6182": 470000000})
        t = module.tdcc_summary(["6182", "2330"])
        self.assertEqual(t["6182"]["date"], "2026-09-24")
        self.assertEqual((t["6182"]["bigPct"], t["6182"]["bigShares"], t["6182"]["thousandPct"]), (27.6, 130000000, 25.5))
        self.assertAlmostEqual(t["6182"]["bigChangePct"], 8.33, places=2)   # 130,000,000 / 120,000,000 - 1
        self.assertAlmostEqual(t["6182"]["bigChangePp"], 2.1, places=2)
        self.assertEqual(t["6182"]["weeks"], 2)   # 09/24 > 09/19 > 09/12
        self.assertEqual((t["2330"]["weeks"], t["2330"]["bigChangePct"]), (0, -0.5))
        # 第二次跑：本益比已有就不重抓、集保不重複
        n = len(calls)
        result = module.collect_once(now=datetime(2026, 9, 26, 10, 0, tzinfo=TW), fetcher=fetch)
        self.assertEqual(result["peTSE"]["source"], "cached")
        self.assertEqual(result["tdcc"]["added"], [])
        self.assertFalse(any("BWIBBU_d" in u for u in calls[n:]))
        status = module.collector_status()
        self.assertEqual(status["peDates"]["TSE"], ["2026-09-24"])
        self.assertEqual(status["tdccDates"], ["2026-09-24", "2026-09-19", "2026-09-12"])

    def test_collect_survives_failures(self) -> None:
        def fetch(url):
            raise OSError("blocked")
        result = module.collect_once(now=datetime(2026, 9, 26, 10, 0, tzinfo=TW), fetcher=fetch)
        self.assertTrue(all(not v["ok"] for v in result.values()))
        self.assertIn("peTSE: OSError", module.collector_status()["lastError"])
        self.assertEqual(module.latest_pe(["2330"]), {})


if __name__ == "__main__":
    unittest.main()


def test_has_new_data_only_when_something_changed():
    import fundamentals_daily as fd

    before = {"revenueTSE": {"ok": True, "ym": "2026-08"}, "sharesTSE": {"ok": True, "rows": 243}}
    same = {"peTSE": {"ok": True, "source": "cached", "rows": 0}, "peOTC": {"ok": True, "rows": 0},
            "revenueTSE": {"ok": True, "ym": "2026-08"}, "sharesTSE": {"ok": True, "rows": 243}, "tdcc": {"ok": True, "added": []}}
    assert fd._has_new_data(same, before) is False
    assert fd._has_new_data({**same, "peTSE": {"ok": True, "source": "twse", "rows": 240}}, before) is True
    assert fd._has_new_data({**same, "tdcc": {"ok": True, "added": ["2026-10-01"]}}, before) is True
    assert fd._has_new_data({**same, "revenueTSE": {"ok": True, "ym": "2026-09"}}, before) is True
    assert fd._has_new_data({**same, "sharesTSE": {"ok": True, "rows": 250}}, before) is True
    assert fd._has_new_data(same, {}) is True      # 剛啟動：之前什麼都沒有，算有新資料
    assert fd._has_new_data({**same, "peTSE": {"ok": False, "error": "x"}}, before) is False
