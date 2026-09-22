from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

import disposition_stocks as module
import persistent_app

TODAY = date(2026, 9, 22)


class ParsingTests(unittest.TestCase):
    def test_roc_and_gregorian_dates(self) -> None:
        self.assertEqual(module.parse_date("1150922"), date(2026, 9, 22))
        self.assertEqual(module.parse_date("115/09/22"), date(2026, 9, 22))
        self.assertEqual(module.parse_date("2026-09-22"), date(2026, 9, 22))
        self.assertEqual(module.parse_date("20260922"), date(2026, 9, 22))
        self.assertIsNone(module.parse_date(""))
        self.assertIsNone(module.parse_date("n/a"))

    def test_period_text(self) -> None:
        self.assertEqual(module.parse_period("1150923～1151006"), (date(2026, 9, 23), date(2026, 10, 6)))
        self.assertEqual(module.parse_period("115/09/23~115/10/06"), (date(2026, 9, 23), date(2026, 10, 6)))
        self.assertEqual(module.parse_period("2026-09-23至2026-10-06"), (date(2026, 9, 23), date(2026, 10, 6)))
        self.assertEqual(module.parse_period(None), (None, None))

    def test_extract_twse_like_rows_keeps_only_active_ones(self) -> None:
        rows = [
            {"Date": "1150919", "Number": "1", "Code": "8996", "Name": "高力", "DispositionPeriod": "1150922～1151006", "Reason": "連續三次"},
            {"Date": "1150901", "Number": "2", "Code": "2330", "Name": "台積電", "DispositionPeriod": "1150902～1150915", "Reason": "x"},
            {"Date": "1150922", "Number": "3", "Code": "ABC", "Name": "壞代號"},
        ]
        result = module.extract_rows(rows, source="twse", today=TODAY)
        self.assertEqual(sorted(result), ["8996"])
        self.assertEqual(result["8996"]["end"], "2026-10-06")
        self.assertEqual(result["8996"]["reason"], "連續三次")

    def test_extract_tpex_like_rows_with_separate_start_end_and_fallback(self) -> None:
        rows = [
            {"SecuritiesCompanyCode": "3661", "CompanyName": "世芯-KY", "DispositionStartDate": "2026/09/18", "DispositionEndDate": "2026/10/01"},
            {"SecuritiesCompanyCode": "6442", "CompanyName": "光聖", "Date": "1150915"},  # 沒有期間欄：公布起 14 天內當處置中
            {"SecuritiesCompanyCode": "1582", "CompanyName": "信錦", "Date": "1150801"},  # 太久以前
        ]
        result = module.extract_rows(rows, source="tpex", today=TODAY)
        self.assertEqual(sorted(result), ["3661", "6442"])
        self.assertEqual(result["3661"]["start"], "2026-09-18")


class RefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        module._map = {}
        module._status = {"fetchedAt": None, "sources": {}, "count": 0}

    def tearDown(self) -> None:
        module._map = {}

    def test_refresh_merges_both_sources_and_keeps_a_failing_sources_previous_list(self) -> None:
        payloads = {
            module.TWSE_URL: [{"Code": "8996", "Name": "高力", "DispositionPeriod": "1150922～1151006"}],
            module.TPEX_URL: [{"SecuritiesCompanyCode": "3661", "CompanyName": "世芯-KY", "DispositionPeriod": "1150918～1151001"}],
        }
        status = module.refresh(fetcher=lambda url: payloads[url], today=TODAY, punish_fetcher=lambda: None)
        self.assertEqual(status["count"], 2)
        self.assertEqual(status["codes"], ["3661", "8996"])
        self.assertTrue(status["sources"]["twse"]["ok"])
        self.assertIn("Code", status["sources"]["twse"]["fields"])
        self.assertTrue(module.is_disposition("8996"))

        def flaky(url):
            if url == module.TPEX_URL:
                raise RuntimeError("HTTP 503")
            return [{"Code": "2330", "Name": "台積電", "DispositionPeriod": "1150922～1151006"}]

        status = module.refresh(fetcher=flaky, today=TODAY, punish_fetcher=lambda: None)
        self.assertEqual(status["codes"], ["2330", "3661"])  # 櫃買那邊失敗就沿用上次的 3661
        self.assertFalse(status["sources"]["tpex"]["ok"])
        self.assertIn("HTTP 503", status["sources"]["tpex"]["error"])


class PunishTests(unittest.TestCase):
    def setUp(self) -> None:
        module._map = {}
        module._status = {"fetchedAt": None, "sources": {}, "count": 0}

    def tearDown(self) -> None:
        module._map = {}

    @staticmethod
    def _punish():
        # Shioaji api.punish() 是欄狀資料：每個欄位一個 list，日期是 date／datetime 物件。
        from datetime import datetime as dt
        from types import SimpleNamespace

        payload = SimpleNamespace(
            code=["8996", "3661", "2330", "ABC"],
            start_date=[date(2026, 9, 22), date(2026, 9, 18), date(2026, 9, 1), date(2026, 9, 22)],
            end_date=[date(2026, 10, 6), dt(2026, 10, 1, 0, 0), date(2026, 9, 15), date(2026, 10, 6)],
            updated_at=[None, None, None, None],
            interval=["5", "5", "5", "5"],
            unit_limit=[None, None, None, None],
            total_limit=[None, None, None, None],
            description=["連續三次", "第二次處置", "x", "壞代號"],
            announced_date=[date(2026, 9, 19), date(2026, 9, 17), date(2026, 8, 30), date(2026, 9, 19)],
        )
        payload.keys = lambda: ["code", "start_date", "end_date", "description", "announced_date"]
        return payload

    def test_extract_punish_columns_keeps_only_active_valid_codes(self) -> None:
        result = module.extract_punish(self._punish(), today=TODAY)
        self.assertEqual(sorted(result), ["3661", "8996"])
        self.assertEqual(result["8996"]["end"], "2026-10-06")
        self.assertEqual(result["8996"]["reason"], "連續三次")
        self.assertEqual(result["3661"]["end"], "2026-10-01")
        self.assertEqual(result["3661"]["source"], "shioaji")
        self.assertEqual(module.extract_punish(None, today=TODAY), {})
        self.assertEqual(module.extract_punish({"code": []}, today=TODAY), {})

    def test_refresh_uses_shioaji_punish_when_tpex_is_blocked(self) -> None:
        def fetcher(url):
            if url == module.TPEX_URL:
                raise RuntimeError("HTTPError: HTTP Error 403: Forbidden")
            return [{"Code": "8996", "Name": "高力", "DispositionPeriod": "1150922～1151006"}]

        status = module.refresh(fetcher=fetcher, today=TODAY, punish_fetcher=self._punish)
        self.assertEqual(status["codes"], ["3661", "8996"])  # 櫃買的 3661 靠 shioaji punish 補上
        self.assertTrue(status["sources"]["shioaji"]["ok"])
        self.assertEqual(status["sources"]["shioaji"]["active"], 2)
        self.assertEqual(status["sources"]["shioaji"]["rows"], 4)
        self.assertIn("code", status["sources"]["shioaji"]["fields"])
        self.assertFalse(status["sources"]["tpex"]["ok"])
        self.assertTrue(module.is_disposition("3661"))
        # 兩邊都有的代號保留期間較晚的那筆
        self.assertEqual(module.get_disposition_map()["8996"]["end"], "2026-10-06")

    def test_refresh_reports_not_logged_in_and_keeps_previous_shioaji_rows(self) -> None:
        module.refresh(fetcher=lambda url: [], today=TODAY, punish_fetcher=self._punish)
        self.assertTrue(module.is_disposition("3661"))
        status = module.refresh(fetcher=lambda url: [], today=TODAY, punish_fetcher=lambda: None)
        self.assertFalse(status["sources"]["shioaji"]["ok"])
        self.assertIn("未登入", status["sources"]["shioaji"]["error"])
        self.assertEqual(status["codes"], ["3661", "8996"])  # 沒登入就沿用上一次 shioaji 的清單

        def boom():
            raise RuntimeError("timeout")

        status = module.refresh(fetcher=lambda url: [], today=TODAY, punish_fetcher=boom)
        self.assertIn("timeout", status["sources"]["shioaji"]["error"])
        self.assertEqual(status["codes"], ["3661", "8996"])


class StockFlagsEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(persistent_app.app)
        module._map = {}

    def tearDown(self) -> None:
        module._map = {}

    def test_returns_flags_for_every_group_stock_with_disposition(self) -> None:
        module._map = {"8996": {"code": "8996", "name": "高力", "start": "2026-09-22", "end": "2026-10-06", "reason": "連續三次", "source": "twse"}}

        import stock_trading_eligibility as eligibility_module

        class FakeContract:
            def __init__(self, code):
                self.margin_trading_balance = 1
                self.short_selling_balance = 0 if code == "8996" else 1
                self.day_trade = "Yes"

        class FakeService:
            def _resolve_stock_contract(self, code):
                return FakeContract(code)

        eligibility_module.clear_trading_eligibility_cache()
        # 端點只讀快取：先像背景更新那樣暖一輪，再打端點。
        with patch.object(eligibility_module, "start_trading_eligibility_warmer", lambda provider: False), \
                patch.object(persistent_app, "start_trading_eligibility_warmer", lambda provider: False):
            eligibility_module.warm_trading_eligibility(["8996", "2330"], service=FakeService())
            resp = self.client.get("/api/hub/stock-flags")
        eligibility_module.clear_trading_eligibility_cache()
        data = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(data["stocks"]), 300)
        self.assertEqual(data["stocks"]["8996"]["disposition"], True)
        self.assertEqual(data["stocks"]["8996"]["dispositionUntil"], "2026-10-06")
        self.assertEqual(data["stocks"]["8996"]["shortable"], False)
        self.assertEqual(data["stocks"]["2330"]["disposition"], False)
        self.assertTrue(data["stocks"]["2330"]["hasStockFutures"])
        self.assertIsNone(data["stocks"]["1101"]["marginable"])  # 沒暖到的代號是「還不知道」，不是 false
        self.assertEqual(data["eligibilityWarmer"]["resolved"], 2)
        self.assertEqual(data["dispositionCodes"], ["8996"])
        self.assertIn("sources", data["disposition"])


if __name__ == "__main__":
    unittest.main()
