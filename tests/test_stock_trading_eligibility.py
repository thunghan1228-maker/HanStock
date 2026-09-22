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

    def test_inspect_contract_reports_raw_fields(self):
        service = FakeService(FakeContract(margin_trading_balance=5, short_selling_balance=0, day_trade="OnlyBuy"))
        info = module.inspect_contract("2330", service=service)
        self.assertEqual(info["contract"]["day_trade"], "onlybuy")
        self.assertEqual(info["contract"]["margin_trading_balance"], 5)
        self.assertTrue(info["contract"]["full"])

    def test_contract_debug_is_a_cached_snapshot_from_the_warmer(self):
        # 端點的 debug 不能在請求路徑碰 Shioaji：暖機還沒跑就是 pending，跑過就是上一輪的檢查結果。
        self.assertTrue(module.contract_debug("2330")["pending"])
        service = FakeService(FakeContract(margin_trading_balance=1, short_selling_balance=1, day_trade="Yes"))
        module.warm_trading_eligibility(["2330", "1101"], service=service)
        debug = module.contract_debug("2330")
        self.assertEqual(debug["code"], "2330")
        self.assertEqual(debug["contract"]["type"], "FakeContract")
        self.assertTrue(debug["cached"]["marginable"])
        self.assertNotIn("pending", debug)

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
                # 2330 成數是小數且當天額度單位已用完（0）：成數 > 0 就算可；8996 全 0 → 不可
                return [Row("2330", 0.6, 0.9, margin_unit=0, short_unit=0), Row("8996", 0, 0)]

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
        # 請求路徑（deep=False）只看合約物件本身：BaseContract 沒欄位 → 還不知道、不快取
        shallow = module.get_trading_eligibility("2330", service=service)
        self.assertIsNone(shallow["dayTradeEligible"])
        self.assertIsNone(module.peek_trading_eligibility("2330"))
        # 背景暖機（deep=True）才走 Contracts.Stocks
        result = module.get_trading_eligibility("2330", service=service, deep=True)
        self.assertTrue(result["marginable"])
        self.assertFalse(result["shortable"])
        self.assertTrue(result["dayTradeEligible"])
        self.assertEqual(result["source"], "stocks")
        self.assertTrue(module.peek_trading_eligibility("2330")["marginable"])
        # 清單裡還沒有的代號維持 BaseContract → 還不知道、不快取
        unknown = module.get_trading_eligibility("1101", service=service, deep=True)
        self.assertIsNone(unknown["dayTradeEligible"])
        self.assertIsNone(module.peek_trading_eligibility("1101"))
        info = module.inspect_contract("2330", service=service)
        self.assertEqual(info["contract"]["type"], "BaseContract")
        self.assertFalse(info["contract"]["full"])
        self.assertEqual(info["fullContract"], "FakeContract")
        self.assertIsNone(module.inspect_contract("1101", service=service)["fullContract"])

    def test_contracts_info_row_decides_flags_and_disposition_level(self):
        # Shioaji 1.7 的 api.contracts.info(base)：成數欄位 > 0 就是可融資／可融券，另外帶處置等級。
        from types import SimpleNamespace

        class BaseContract:
            def __init__(self, code):
                self.code = code

        rows = {
            # 正式環境 2026-09-22 的成數是小數（0.6／0.9），不是 60／90
            "2330": SimpleNamespace(day_trade="Yes", margin_loan_ratio=0.6, short_margin_ratio=0.9, short_selling_suspended=False, disposition_level=0, attention_flag=False, trading_suspended=False),
            "8996": SimpleNamespace(day_trade="No", margin_loan_ratio=0, short_margin_ratio=0, short_selling_suspended=False, disposition_level=1, attention_flag=True, trading_suspended=False),
            "2454": SimpleNamespace(day_trade="OnlyBuy", margin_loan_ratio=60, short_margin_ratio=90, short_selling_suspended=True, disposition_level=0, attention_flag=False, trading_suspended=False),
        }

        class Contracts:
            def __init__(self):
                self.info_calls = []

            def get(self, code):
                return BaseContract(code)

            def info(self, base):
                self.info_calls.append(base.code)
                return rows.get(base.code)

        class InfoService:
            def __init__(self):
                self.api = SimpleNamespace(contracts=Contracts())

        service = InfoService()
        status = module.warm_trading_eligibility(["2330", "8996", "2454", "1101"], service=service)
        self.assertEqual(status["resolved"], 3)
        self.assertEqual(status["unknown"], 1)
        self.assertTrue(status["paths"]["info"]["enabled"])
        self.assertGreaterEqual(status["paths"]["info"]["calls"], 4)
        tsmc = module.peek_trading_eligibility("2330")
        self.assertEqual((tsmc["marginable"], tsmc["shortable"], tsmc["dayTradeEligible"], tsmc["dispositionLevel"]), (True, True, True, 0))
        self.assertEqual(tsmc["source"], "info")
        gaoli = module.peek_trading_eligibility("8996")
        self.assertEqual((gaoli["marginable"], gaoli["shortable"], gaoli["dayTradeEligible"], gaoli["dispositionLevel"]), (False, False, False, 1))
        self.assertTrue(gaoli["attention"])
        mediatek = module.peek_trading_eligibility("2454")
        self.assertTrue(mediatek["marginable"])
        self.assertFalse(mediatek["shortable"])  # 暫停融券
        self.assertTrue(mediatek["dayTradeEligible"])
        self.assertIsNone(module.peek_trading_eligibility("1101"))
        debug = module.contract_debug()
        self.assertEqual(debug["info"]["margin_loan_ratio"], 0.6)
        self.assertEqual(debug["info"]["day_trade"], "Yes")

    def test_slow_background_path_is_disabled_for_the_rest_of_the_round(self):
        from types import SimpleNamespace

        class Contracts:
            def __init__(self):
                self.info_calls = 0

            def get(self, code):
                return SimpleNamespace(code=code)

            def info(self, base):
                self.info_calls += 1
                return SimpleNamespace(day_trade="Yes", margin_loan_ratio=60, short_margin_ratio=90)

        service = SimpleNamespace(api=SimpleNamespace(contracts=Contracts()))
        with patch.object(module, "SLOW_CALL_SECONDS", 0.0):  # 任何一次呼叫都算太慢
            status = module.warm_trading_eligibility(["2330", "2317", "2454"], service=service)
        self.assertEqual(service.api.contracts.info_calls, 1)
        self.assertFalse(status["paths"]["info"]["enabled"])
        self.assertIn("秒", status["paths"]["info"]["disabledReason"])
        # 第一檔還是查到了；後面兩檔這一輪先跳過，下一輪重新啟用
        self.assertTrue(module.peek_trading_eligibility("2330")["marginable"])
        self.assertIsNone(module.peek_trading_eligibility("2317"))
        status = module.warm_trading_eligibility(["2317"], service=service)
        self.assertTrue(status["paths"]["info"]["enabled"])
        self.assertTrue(module.peek_trading_eligibility("2317")["marginable"])

    def test_failing_background_path_is_disabled_after_repeated_errors(self):
        from types import SimpleNamespace

        class Contracts:
            def __init__(self):
                self.info_calls = 0

            def get(self, code):
                return SimpleNamespace(code=code)

            def info(self, base):
                self.info_calls += 1
                raise RuntimeError("Contracts not fetched")

        service = SimpleNamespace(api=SimpleNamespace(contracts=Contracts()))
        status = module.warm_trading_eligibility(["2330", "2317", "2454", "1101", "1216"], service=service)
        self.assertEqual(service.api.contracts.info_calls, module.PATH_MAX_ERRORS)
        self.assertFalse(status["paths"]["info"]["enabled"])
        self.assertIn("Contracts not fetched", status["paths"]["info"]["disabledReason"])
        self.assertEqual(status["unknown"], 5)
        self.assertEqual(module.contract_debug()["info"]["error"], status["paths"]["info"]["disabledReason"])

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
        self.assertEqual(module.inspect_contract("2330", service=service)["credit"]["marginable"], True)

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
