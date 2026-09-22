import unittest

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
