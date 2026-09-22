"""Shioaji 1.7 合約查找：api.contracts.get 只回 BaseContract，完整合約要從 Contracts.Stocks 拿。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import shioaji_contracts as module


class BaseContract:
    def __init__(self, code):
        self.code = code
        self.security_type = "STK"


class Stock(BaseContract):
    def __init__(self, code, day_trade="Yes"):
        super().__init__(code)
        self.day_trade = day_trade
        self.margin_trading_balance = 1
        self.short_selling_balance = 1


class StocksByGetItem:
    def __init__(self, contracts):
        self._contracts = contracts

    def __getitem__(self, code):
        return self._contracts[code]


class StocksByGet(StocksByGetItem):
    def get(self, code, default=None):
        return self._contracts.get(code, default)


class ContractsGet:
    def __init__(self, status="Fetching"):
        self.calls = []
        self.status = SimpleNamespace(value=status)

    def get(self, code):
        self.calls.append(code)
        return BaseContract(code)


class ShioajiContractsTests(unittest.TestCase):
    def test_full_contract_is_preferred_over_base_contract(self):
        api = SimpleNamespace(contracts=ContractsGet(), Contracts=SimpleNamespace(Stocks=StocksByGetItem({"2330": Stock("2330")})))
        contract = module.resolve_stock_contract(api, "2330")
        self.assertIsInstance(contract, Stock)
        self.assertEqual(api.contracts.calls, [])  # 完整合約拿得到就不用再問 contracts.get

    def test_falls_back_to_base_contract_while_contracts_are_still_downloading(self):
        api = SimpleNamespace(contracts=ContractsGet(), Contracts=SimpleNamespace(Stocks=StocksByGetItem({})))
        contract = module.resolve_stock_contract(api, "2330")
        self.assertIsInstance(contract, BaseContract)
        self.assertNotIsInstance(contract, Stock)
        self.assertFalse(module.is_full_contract(contract))

    def test_stocks_container_with_get_method_and_lowercase_names(self):
        api = SimpleNamespace(contracts=SimpleNamespace(stocks=StocksByGet({"8996": Stock("8996")})))
        self.assertIsInstance(module.full_stock_contract(api, "8996"), Stock)
        self.assertIsNone(module.full_stock_contract(api, "9999"))

    def test_upgrade_replaces_base_contract_but_keeps_full_one(self):
        stocks = StocksByGetItem({"2330": Stock("2330")})
        api = SimpleNamespace(contracts=ContractsGet(), Contracts=SimpleNamespace(Stocks=stocks))
        base = BaseContract("2330")
        self.assertIsInstance(module.upgrade_contract(api, "2330", base), Stock)
        full = Stock("2330", day_trade="No")
        self.assertIs(module.upgrade_contract(api, "2330", full), full)
        self.assertIs(module.upgrade_contract(api, "1101", base), base)  # 清單裡沒有就維持原樣

    def test_api_without_stock_categories_only_uses_contracts_get(self):
        api = SimpleNamespace(contracts=ContractsGet())
        contract = module.resolve_stock_contract(api, "2330")
        self.assertIsInstance(contract, BaseContract)
        self.assertEqual(api.contracts.calls, ["2330"])
        self.assertIsNone(module.resolve_stock_contract(None, "2330"))

    def test_fetch_status_reads_enum_value(self):
        api = SimpleNamespace(contracts=ContractsGet(status="Fetched"))
        self.assertEqual(module.contracts_fetch_status(api), "Fetched")
        self.assertIsNone(module.contracts_fetch_status(SimpleNamespace(contracts=SimpleNamespace())))
        self.assertIsNone(module.contracts_fetch_status(None))


if __name__ == "__main__":
    unittest.main()
