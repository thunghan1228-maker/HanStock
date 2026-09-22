"""Shioaji 1.7 合約查找：api.contracts.get 只回 BaseContract，欄位要靠 contracts.info 或 Contracts.Stocks。"""

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

    def __getattr__(self, name):  # 編譯版 ContractCategory：沒有 get，未知屬性會說 has no group
        raise AttributeError(f"ContractCategory has no group '{name}'")


class StocksByGet:
    def __init__(self, contracts):
        self._contracts = contracts

    def get(self, code, default=None):
        return self._contracts.get(code, default)


class ContractsGet:
    def __init__(self, status="Fetching", info=None):
        self.calls = []
        self.status = SimpleNamespace(value=status)
        self._info = info

    def get(self, code):
        self.calls.append(code)
        return BaseContract(code)

    def info(self, base):
        if self._info is None:
            raise RuntimeError("Contracts not fetched")
        return self._info


class ShioajiContractsTests(unittest.TestCase):
    def test_resolve_uses_base_contract_first_like_the_old_code_path(self):
        api = SimpleNamespace(contracts=ContractsGet(), Contracts=SimpleNamespace(Stocks=StocksByGetItem({"2330": Stock("2330")})))
        contract = module.resolve_stock_contract(api, "2330")
        self.assertIsInstance(contract, BaseContract)
        self.assertNotIsInstance(contract, Stock)  # 訂閱用 BaseContract 就夠，不碰成本未知的 Contracts.Stocks
        self.assertEqual(api.contracts.calls, ["2330"])

    def test_resolve_falls_back_to_contracts_stocks_when_get_returns_nothing(self):
        class NoBase(ContractsGet):
            def get(self, code):
                return None

        api = SimpleNamespace(contracts=NoBase(), Contracts=SimpleNamespace(Stocks=StocksByGetItem({"2330": Stock("2330")})))
        self.assertIsInstance(module.resolve_stock_contract(api, "2330"), Stock)
        self.assertIsNone(module.resolve_stock_contract(api, "9999"))
        self.assertIsNone(module.resolve_stock_contract(None, "2330"))

    def test_resolve_swallows_container_errors(self):
        class Boom(ContractsGet):
            def get(self, code):
                raise RuntimeError("not logged in")

        api = SimpleNamespace(contracts=Boom(), Contracts=SimpleNamespace(Stocks=StocksByGetItem({})))
        self.assertIsNone(module.resolve_stock_contract(api, "2330"))

    def test_full_stock_contract_handles_get_method_lowercase_names_and_missing_codes(self):
        api = SimpleNamespace(contracts=SimpleNamespace(stocks=StocksByGet({"8996": Stock("8996")})))
        self.assertIsInstance(module.full_stock_contract(api, "8996"), Stock)
        self.assertIsNone(module.full_stock_contract(api, "9999"))
        api = SimpleNamespace(Contracts=SimpleNamespace(Stocks=StocksByGetItem({"2330": Stock("2330")})))
        self.assertIsInstance(module.full_stock_contract(api, "2330"), Stock)
        self.assertIsNone(module.full_stock_contract(api, "1101"))  # KeyError → None
        self.assertIsNone(module.full_stock_contract(SimpleNamespace(contracts=ContractsGet()), "2330"))  # 沒有 Stocks 類別

    def test_full_stock_contract_lets_unexpected_errors_surface_for_diagnostics(self):
        class BrokenStocks:
            def __getitem__(self, code):
                raise RuntimeError("Cannot fetch contracts: timeout")

        api = SimpleNamespace(Contracts=SimpleNamespace(Stocks=BrokenStocks()))
        with self.assertRaises(RuntimeError):
            module.full_stock_contract(api, "2330")

    def test_is_full_contract(self):
        self.assertTrue(module.is_full_contract(Stock("2330")))
        self.assertFalse(module.is_full_contract(BaseContract("2330")))
        self.assertFalse(module.is_full_contract(None))

    def test_contracts_info_returns_row_or_none_without_the_api(self):
        row = SimpleNamespace(day_trade="Yes", margin_loan_ratio=60)
        api = SimpleNamespace(contracts=ContractsGet(info=row))
        self.assertIs(module.contracts_info(api, BaseContract("2330")), row)
        self.assertIsNone(module.contracts_info(SimpleNamespace(contracts=SimpleNamespace()), BaseContract("2330")))
        self.assertIsNone(module.contracts_info(api, None))
        with self.assertRaises(RuntimeError):
            module.contracts_info(SimpleNamespace(contracts=ContractsGet()), BaseContract("2330"))

    def test_fetch_status_reads_enum_value(self):
        api = SimpleNamespace(contracts=ContractsGet(status="Fetched"))
        self.assertEqual(module.contracts_fetch_status(api), "Fetched")
        self.assertIsNone(module.contracts_fetch_status(SimpleNamespace(contracts=SimpleNamespace())))
        self.assertIsNone(module.contracts_fetch_status(None))


if __name__ == "__main__":
    unittest.main()
