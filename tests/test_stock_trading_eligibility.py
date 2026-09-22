import unittest
from unittest.mock import patch

import stock_trading_eligibility as module


class FakeContract:
    def __init__(self, margin_trading_balance=1, short_selling_balance=1, day_trade="Yes"):
        self.margin_trading_balance = margin_trading_balance
        self.short_selling_balance = short_selling_balance
        self.day_trade = day_trade


class FakeService:
    def __init__(self, contract):
        self._contract = contract

    def _resolve_stock_contract(self, code):
        return self._contract


class TradingEligibilityTests(unittest.TestCase):
    def setUp(self):
        module.clear_trading_eligibility_cache()

    def test_has_stock_futures_checks_static_group_membership(self):
        futures_code = next(iter(module._FUTURES_UNDERLYING_CODES))
        self.assertTrue(module.has_stock_futures(futures_code))
        self.assertFalse(module.has_stock_futures("非股期標的999"))

    def test_reads_marginable_shortable_and_day_trade_from_contract(self):
        service = FakeService(FakeContract(margin_trading_balance=1, short_selling_balance=0, day_trade="Yes"))
        result = module.get_trading_eligibility("2330", service=service)
        self.assertTrue(result["marginable"])
        self.assertFalse(result["shortable"])
        self.assertTrue(result["dayTradeEligible"])

    def test_only_buy_day_trade_counts_as_eligible(self):
        service = FakeService(FakeContract(day_trade="OnlyBuy"))
        result = module.get_trading_eligibility("2330", service=service)
        self.assertTrue(result["dayTradeEligible"])

    def test_no_day_trade_is_not_eligible(self):
        service = FakeService(FakeContract(day_trade="No"))
        result = module.get_trading_eligibility("2330", service=service)
        self.assertFalse(result["dayTradeEligible"])

    def test_zero_balance_flags_are_falsy(self):
        service = FakeService(FakeContract(margin_trading_balance=0, short_selling_balance=0))
        result = module.get_trading_eligibility("2330", service=service)
        self.assertFalse(result["marginable"])
        self.assertFalse(result["shortable"])

    def test_missing_contract_leaves_fields_none_but_still_reports_futures_flag(self):
        service = FakeService(None)
        result = module.get_trading_eligibility("2330", service=service)
        self.assertIsNone(result["marginable"])
        self.assertIsNone(result["shortable"])
        self.assertIsNone(result["dayTradeEligible"])
        self.assertIn("hasStockFutures", result)

    def test_result_is_cached_per_code(self):
        service = FakeService(FakeContract())
        first = module.get_trading_eligibility("2330", service=service)
        service._contract = None  # 改變底層合約也不該影響已經快取的結果
        second = module.get_trading_eligibility("2330", service=service)
        self.assertEqual(first, second)

    def test_unfetched_contract_fields_are_unknown_and_not_cached(self):
        # 正式環境 2026-09-22：登入後合約清單還沒下載完，Contract 拿得到但 day_trade 是空字串、
        # 餘額 0，舊版把它當成「不可融資／不可當沖」永久快取，整天每一檔都顯示不可。
        blank = FakeContract(margin_trading_balance=0, short_selling_balance=0, day_trade="")
        service = FakeService(blank)
        first = module.get_trading_eligibility("2330", service=service)
        self.assertIsNone(first["marginable"])
        self.assertIsNone(first["shortable"])
        self.assertIsNone(first["dayTradeEligible"])

        service._contract = FakeContract(margin_trading_balance=1, short_selling_balance=1, day_trade="Yes")
        second = module.get_trading_eligibility("2330", service=service)
        self.assertTrue(second["marginable"])
        self.assertTrue(second["dayTradeEligible"])

    def test_cache_resets_on_a_new_trading_day(self):
        service = FakeService(FakeContract(margin_trading_balance=1, day_trade="Yes"))
        self.assertTrue(module.get_trading_eligibility("2330", service=service)["marginable"])
        service._contract = FakeContract(margin_trading_balance=0, day_trade="Yes")
        module._cache_day = "2000-01-01"  # 假裝跨日
        self.assertFalse(module.get_trading_eligibility("2330", service=service)["marginable"])

    def test_contract_debug_reports_raw_fields(self):
        service = FakeService(FakeContract(margin_trading_balance=5, short_selling_balance=0, day_trade="OnlyBuy"))
        info = module.contract_debug("2330", service=service)
        self.assertEqual(info["contract"]["day_trade"], "onlybuy")
        self.assertEqual(info["contract"]["margin_trading_balance"], 5)

    def test_credit_enquires_decide_margin_and_short_even_when_contract_balances_are_zero(self):
        # 正式環境：合約物件的融資券餘額欄位整天是 0，改以永豐信用額度查詢的成數為準。
        class Row:
            def __init__(self, stock_id, margin_loan_ratio, short_margin_ratio, margin_unit=0, short_unit=0):
                self.stock_id = stock_id
                self.margin_loan_ratio = margin_loan_ratio
                self.short_margin_ratio = short_margin_ratio
                self.margin_unit = margin_unit
                self.short_unit = short_unit

        class FakeApi:
            def __init__(self):
                self.calls = []

            def credit_enquires(self, contracts, timeout=30000):
                self.calls.append(len(contracts))
                return [Row("2330", 60, 90, margin_unit=100, short_unit=5), Row("8996", 0, 0)]

        class CreditService(FakeService):
            def __init__(self, contract):
                super().__init__(contract)
                self.api = FakeApi()

        service = CreditService(FakeContract(margin_trading_balance=0, short_selling_balance=0, day_trade="Yes"))
        status = module.warm_trading_eligibility(["2330", "8996"], service=service)
        self.assertEqual(status["credit"]["resolved"], 2)
        self.assertEqual(status["credit"]["batches"], 1)
        self.assertIsNone(status["credit"]["error"])
        self.assertTrue(module.get_trading_eligibility("2330", service=service)["marginable"])
        self.assertTrue(module.get_trading_eligibility("2330", service=service)["shortable"])
        self.assertFalse(module.get_trading_eligibility("8996", service=service)["marginable"])
        self.assertFalse(module.get_trading_eligibility("8996", service=service)["shortable"])
        self.assertTrue(module.get_trading_eligibility("8996", service=service)["dayTradeEligible"])
        # 30 分鐘內再暖一次不會重打信用查詢
        module.warm_trading_eligibility(["2330", "8996"], service=service)
        self.assertEqual(service.api.calls, [2])

    def test_credit_enquiry_failure_is_reported_and_contract_fields_still_used(self):
        class FakeApi:
            def credit_enquires(self, contracts, timeout=30000):
                raise RuntimeError("not logged in")

        class CreditService(FakeService):
            def __init__(self, contract):
                super().__init__(contract)
                self.api = FakeApi()

        service = CreditService(FakeContract(margin_trading_balance=1, short_selling_balance=1, day_trade="Yes"))
        status = module.warm_trading_eligibility(["2330"], service=service)
        self.assertIn("not logged in", status["credit"]["error"])
        self.assertTrue(module.get_trading_eligibility("2330", service=service)["marginable"])

    def test_base_contract_from_resolver_is_upgraded_via_contracts_stocks(self):
        # 正式環境 2026-09-22：服務的解析器回 BaseContract（只有代號／交易所，沒有 day_trade 等欄位），
        # 融資券／當沖整天都是「不知道」；要再從 Contracts.Stocks 換成完整合約。
        from types import SimpleNamespace

        class BaseContract:
            def __init__(self, code):
                self.code = code
                self.security_type = "STK"

        class Stocks:
            def __getitem__(self, code):
                if code != "2330":
                    raise KeyError(code)
                return FakeContract(margin_trading_balance=1, short_selling_balance=0, day_trade="OnlyBuy")

        class BaseService(FakeService):
            def __init__(self):
                super().__init__(None)
                self.api = SimpleNamespace(Contracts=SimpleNamespace(Stocks=Stocks()))

            def _resolve_stock_contract(self, code):
                return BaseContract(code)

        service = BaseService()
        result = module.get_trading_eligibility("2330", service=service)
        self.assertTrue(result["marginable"])
        self.assertFalse(result["shortable"])
        self.assertTrue(result["dayTradeEligible"])
        # 清單裡還沒有的代號維持 BaseContract → 還不知道、不快取
        unknown = module.get_trading_eligibility("1101", service=service)
        self.assertIsNone(unknown["dayTradeEligible"])
        self.assertIsNone(module.peek_trading_eligibility("1101"))
        info = module.contract_debug("2330", service=service)
        self.assertEqual(info["contract"]["type"], "FakeContract")
        self.assertTrue(info["contract"]["full"])
        self.assertEqual(info["fullContract"], "FakeContract")
        self.assertIsNone(module.contract_debug("1101", service=service)["fullContract"])

    def test_credit_enquiry_reports_codes_the_broker_did_not_return(self):
        class Row:
            def __init__(self, stock_id):
                self.stock_id = stock_id
                self.margin_loan_ratio = 60
                self.short_margin_ratio = 90
                self.margin_unit = 0
                self.short_unit = 0

        class FakeApi:
            def credit_enquires(self, contracts, timeout=30000):
                return [Row("2330")]

        class CreditService(FakeService):
            def __init__(self, contract):
                super().__init__(contract)
                self.api = FakeApi()

        service = CreditService(FakeContract(margin_trading_balance=0, short_selling_balance=0, day_trade="Yes"))
        status = module.refresh_credit_flags(["2330", "8996"], service=service)
        self.assertEqual(status["resolved"], 1)
        self.assertEqual(status["missing"], 1)
        self.assertEqual(status["missingSample"], ["8996"])
        self.assertTrue(module.get_trading_eligibility("2330", service=service)["marginable"])
        # 沒回的那檔維持合約欄位的判讀（餘額 0 → 不可），不會誤判
        self.assertFalse(module.get_trading_eligibility("8996", service=service)["marginable"])
        self.assertEqual(module.contract_debug("2330", service=service)["credit"]["marginable"], True)

    def test_first_credit_refresh_runs_even_when_process_uptime_is_short(self):
        import time as time_module

        class FakeApi:
            def __init__(self):
                self.calls = 0

            def credit_enquires(self, contracts, timeout=30000):
                self.calls += 1
                return []

        class CreditService(FakeService):
            def __init__(self, contract):
                super().__init__(contract)
                self.api = FakeApi()

        service = CreditService(FakeContract())
        with patch.object(time_module, "monotonic", lambda: 12.0):  # 剛開機 12 秒，遠小於 30 分鐘冷卻
            module.refresh_credit_flags(["2330"], service=service)
        self.assertEqual(service.api.calls, 1)

    def test_clear_cache_forces_refetch(self):
        service = FakeService(FakeContract(margin_trading_balance=1))
        first = module.get_trading_eligibility("2330", service=service)
        self.assertTrue(first["marginable"])
        service._contract = FakeContract(margin_trading_balance=0)
        module.clear_trading_eligibility_cache()
        second = module.get_trading_eligibility("2330", service=service)
        self.assertFalse(second["marginable"])


if __name__ == "__main__":
    unittest.main()
