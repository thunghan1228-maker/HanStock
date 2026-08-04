from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path


class FakeQuoteType:
    Tick = "tick"


class FakeExchange:
    TSE = "TSE"


class FakeQuoteEvents:
    def on_event(self, func):
        self.callback = func
        return func


@dataclass
class FakeContract:
    code: str
    security_type: str = "STK"
    target_code: str | None = None


class FakeContractsAPI:
    def get(self, code):
        if code.startswith("BAD"):
            return None
        if code == "TXFR1":
            return FakeContract("TXFR1", "FUT", "TXFH6")
        return FakeContract(code)


class FakeAPI:
    def __init__(self, simulation=False):
        self.simulation = simulation
        self.contracts = FakeContractsAPI()
        self.quote = FakeQuoteEvents()
        self.subscribed = []
        self.unsubscribed = []

    def subscribe(self, contract, quote_type):
        self.subscribed.append((contract.code, quote_type))

    def unsubscribe(self, contract, quote_type):
        self.unsubscribed.append((contract.code, quote_type))

    def on_tick_fop_v1(self):
        return lambda func: func

    def on_tick_stk_v1(self):
        return lambda func: func


fake_sj = types.ModuleType("shioaji")
fake_sj.Shioaji = FakeAPI
fake_sj.QuoteType = FakeQuoteType
fake_sj.Exchange = FakeExchange
fake_sj.TickFOPv1 = object
fake_sj.TickSTKv1 = object
sys.modules["shioaji"] = fake_sj

MODULE_PATH = Path(__file__).parents[1] / "quote_service.py"
spec = importlib.util.spec_from_file_location("quote_service_under_test", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeTick:
    code = "2330"
    datetime = (2026, 8, 4, 9, 12, 30, 123456)
    open = 900
    avg_price = 905.5
    close = 910
    high = 915
    low = 898
    amount = 910000
    total_amount = 99999999
    volume = 1
    total_volume = 12345
    tick_type = 1
    chg_type = 2
    price_chg = 10
    pct_chg = 111  # 1.11%
    bid_side_total_vol = 6000
    ask_side_total_vol = 5000
    bid_side_total_cnt = 300
    ask_side_total_cnt = 250
    suspend = False
    simtrade = False
    intraday_odd = False


class QuoteServiceStockTests(unittest.TestCase):
    def setUp(self):
        os.environ["SHIOAJI_STOCK_MAX_SUBSCRIPTIONS"] = "2"
        self.service = module.QuoteService()
        self.service.api = FakeAPI()
        self.service.state.logged_in = True

    def test_stock_tick_normalizes_pct_chg(self):
        data = self.service._stock_tick_to_dict(FakeExchange.TSE, FakeTick())
        self.assertEqual(data["code"], "2330")
        self.assertEqual(data["pct_chg"], 1.11)
        self.assertIn("2026-08-04T09:12:30.123456+08:00", data["tick_time"])

    def test_subscription_is_idempotent(self):
        first = self.service.ensure_stock_subscriptions(["2330", "2344"])
        second = self.service.ensure_stock_subscriptions(["2330"])
        self.assertEqual(first["newly_subscribed"], ["2330", "2344"])
        self.assertEqual(second["already_subscribed"], ["2330"])
        self.assertEqual(len(self.service.api.subscribed), 2)

    def test_lru_evicts_old_subscription(self):
        self.service.ensure_stock_subscriptions(["2330", "2344"])
        result = self.service.ensure_stock_subscriptions(["2408"])
        self.assertEqual(result["newly_subscribed"], ["2408"])
        self.assertEqual(result["evicted"], ["2330"])
        self.assertIn(("2330", "tick"), self.service.api.unsubscribed)

    def test_invalid_contract_returns_failure(self):
        result = self.service.ensure_stock_subscriptions(["BAD1"])
        self.assertIn("BAD1", result["failed"])


if __name__ == "__main__":
    unittest.main()
