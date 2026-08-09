from __future__ import annotations

import unittest
from types import SimpleNamespace

import stock_futures_service as service
from stock_futures_standard_policy import resolve_front_month_contract


class FakeContracts:
    def __init__(self, rows):
        self.rows = rows

    def get(self, code):
        return SimpleNamespace(code=str(code).strip().upper())

    def futures_by_underlying(self, stock):
        return list(self.rows)


class FakeApi:
    def __init__(self, rows):
        self.contracts = FakeContracts(rows)


def contract(code: str, month: str, size: int = 2000, name: str = "股票期貨"):
    return SimpleNamespace(
        code=code,
        target_code=code.replace("R1", "H6"),
        delivery_month=month,
        underlying_code="1717",
        contract_size=size,
        name=name,
    )


class StockFuturesStandardPolicyTests(unittest.TestCase):
    def test_same_month_regular_prefers_standard_over_adjusted(self):
        api = FakeApi([
            contract("QO1R1", "202608"),
            contract("QOFR1", "202608"),
        ])
        picked = resolve_front_month_contract(api, "1717", "regular")
        self.assertEqual(picked.code, "QOFR1")

    def test_same_month_mini_prefers_standard_over_adjusted(self):
        api = FakeApi([
            contract("QA1R1", "202608", 100, "小型股票期貨"),
            contract("QAFR1", "202608", 100, "小型股票期貨"),
        ])
        picked = resolve_front_month_contract(api, "1717", "mini")
        self.assertEqual(picked.code, "QAFR1")

    def test_nearest_month_still_beats_standard_preference(self):
        api = FakeApi([
            contract("QO1R1", "202608"),
            contract("QOFR1", "202609"),
        ])
        picked = resolve_front_month_contract(api, "1717", "regular")
        self.assertEqual(picked.code, "QO1R1")

    def test_policy_is_installed_on_service_module(self):
        self.assertIs(service.resolve_front_month_contract, resolve_front_month_contract)


if __name__ == "__main__":
    unittest.main()
