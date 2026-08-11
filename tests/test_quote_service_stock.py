from __future__ import annotations

import importlib.util
import os
import sys
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch


class FakeQuoteType:
    Tick = "tick"
    Quote = "quote"


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
        self.logged_in = False
        self.logged_out = False

    def login(self, api_key=None, secret_key=None, subscribe_trade=False):
        self.logged_in = True
        self.subscribe_trade = subscribe_trade
        return []

    def logout(self):
        self.logged_out = True
        self.logged_in = False

    def on_quote_fop_v1(self):
        return lambda func: func

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
        os.environ["SHIOAJI_MAIN_STOCK_MAX_SUBSCRIPTIONS"] = "2"
        os.environ["SHIOAJI_SHARED_QUOTE_POOL_SIZE"] = "2"
        os.environ["SHIOAJI_SHARED_PER_CONNECTION_CAP"] = "100"
        os.environ["SHIOAJI_API_KEY"] = "fake-key"
        os.environ["SHIOAJI_SECRET_KEY"] = "fake-secret"
        import stock_futures_service

        stock_futures_service._service = None
        self.service = module.QuoteService()
        self.service.api = FakeAPI()
        self.service.state.logged_in = True

    def test_transient_p2p_recovery_forces_main_reconnect(self):
        calls = []
        self.service.state.quote_connected = True
        self.service.state.subscribed = True
        self.service._trigger_reconnect = lambda: calls.append("reconnect")

        self.service.recover_transient_p2p_session("test P2P failure")

        self.assertFalse(self.service.state.quote_connected)
        self.assertFalse(self.service.state.subscribed)
        self.assertEqual(self.service.state.error_message, "test P2P failure")
        self.assertEqual(calls, ["reconnect"])

    @patch.dict(
        os.environ,
        {
            "RAILWAY_PROJECT_ID": "primary-project",
            "HANSTOCK_PRIMARY_RAILWAY_PROJECT_ID": "primary-project",
            "SHIOAJI_RAILWAY_STARTUP_DELAY_SECONDS": "18",
        },
        clear=False,
    )
    def test_primary_railway_deployment_delays_shioaji_login(self):
        self.assertEqual(module.quote_startup_delay_seconds(), 18.0)

    def test_market_data_login_does_not_subscribe_trade_events(self):
        self.service.state.initialized = True
        self.service._login()

        self.assertTrue(self.service.state.logged_in)
        self.assertFalse(self.service.api.subscribe_trade)

    def tearDown(self):
        import stock_futures_service

        if stock_futures_service._service is not None:
            stock_futures_service._service.shutdown()
        stock_futures_service._service = None

    def test_stock_tick_normalizes_pct_chg(self):
        data = self.service._stock_tick_to_dict(FakeExchange.TSE, FakeTick())
        self.assertEqual(data["code"], "2330")
        self.assertEqual(data["pct_chg"], 1.11)
        self.assertIn("2026-08-04T09:12:30.123456+08:00", data["tick_time"])

    @patch.dict(
        os.environ,
        {"RAILWAY_PROJECT_ID": "7109048d-fb11-4ddf-bf67-f1bb98ca815e"},
        clear=False,
    )
    def test_standby_railway_project_does_not_login_shioaji(self):
        service = module.QuoteService()
        service.startup()
        self.assertIsNone(service.api)
        self.assertFalse(service.state.logged_in)
        self.assertEqual(service.get_health()["quote_role"], "standby")
        self.assertEqual(service.state.data_source, "standby_no_shioaji_login")

    def test_subscription_is_idempotent(self):
        first = self.service.ensure_stock_subscriptions(["2330", "2344"])
        second = self.service.ensure_stock_subscriptions(["2330"])
        self.assertEqual(first["newly_subscribed"], ["2330", "2344"])
        self.assertEqual(second["already_subscribed"], ["2330"])
        self.assertEqual(len(self.service.api.subscribed), 2)

    def test_extra_index_futures_subscription_is_idempotent_and_restorable(self):
        contract = FakeContract("MXFR1", "FUT", "MXFH6")
        self.assertTrue(self.service.ensure_extra_futures_subscription(contract))
        self.assertTrue(self.service.ensure_extra_futures_subscription(contract))
        self.assertEqual(self.service.api.subscribed, [("MXFR1", "tick")])

        self.service._resubscribe_extra_futures()
        self.assertEqual(self.service.api.subscribed, [("MXFR1", "tick"), ("MXFR1", "tick")])

    def test_capacity_spills_to_shared_pool_without_eviction(self):
        self.service.ensure_stock_subscriptions(["2330", "2344"])
        result = self.service.ensure_stock_subscriptions(["2408"])
        self.assertEqual(result["newly_subscribed"], ["2408"])
        self.assertEqual(result["evicted"], [])
        self.assertEqual(result["active_count"], 3)
        self.assertEqual(result["main_active_count"], 2)
        self.assertEqual(result["shared_active_count"], 1)
        self.assertNotIn(("2330", "tick"), self.service.api.unsubscribed)
        self.assertEqual(self.service.get_active_stock_codes(), ["2330", "2344", "2408"])

    def test_concurrent_batches_do_not_exceed_main_connection_limit(self):
        original_subscribe = self.service.api.subscribe

        def slow_subscribe(contract, quote_type):
            time.sleep(0.01)
            original_subscribe(contract, quote_type)

        self.service.api.subscribe = slow_subscribe
        codes = ["2330", "2344", "2408", "2317"]

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(lambda code: self.service.ensure_stock_subscriptions([code]), codes)
            )

        self.assertTrue(all(result["failed"] == {} for result in results))
        health = self.service.get_stock_health()
        self.assertEqual(health["active_subscription_count"], 4)
        self.assertEqual(health["main_connection_active_count"], 2)
        self.assertEqual(health["shared_pool_active_count"], 2)
        self.assertEqual(len(self.service.api.subscribed), 2)

    def test_invalid_contract_returns_failure(self):
        result = self.service.ensure_stock_subscriptions(["BAD1"])
        self.assertIn("BAD1", result["failed"])

    @patch.dict(
        os.environ,
        {
            "SHIOAJI_MAIN_STOCK_MAX_SUBSCRIPTIONS": "190",
            "SHIOAJI_SHARED_QUOTE_POOL_SIZE": "4",
            "SHIOAJI_SHARED_PER_CONNECTION_CAP": "195",
        },
        clear=False,
    )
    def test_full_664_stock_universe_stays_active_without_lru_eviction(self):
        import stock_futures_service

        if stock_futures_service._service is not None:
            stock_futures_service._service.shutdown()
        stock_futures_service._service = None
        service = module.QuoteService()
        service.api = FakeAPI()
        service.state.logged_in = True
        codes = [f"{1000 + index:04d}" for index in range(664)]

        result = service.ensure_stock_subscriptions(codes)

        self.assertEqual(result["failed"], {})
        self.assertEqual(result["evicted"], [])
        self.assertEqual(result["active_count"], 664)
        self.assertEqual(result["main_active_count"], 190)
        self.assertEqual(result["shared_active_count"], 474)
        self.assertEqual(len(service.get_active_stock_codes()), 664)
        health = service.get_stock_health()
        self.assertEqual(health["active_subscription_count"], 664)
        self.assertEqual(health["eviction_policy"], "disabled")
        self.assertEqual(health["main_connection_active_count"], 190)
        self.assertEqual(health["shared_pool_active_count"], 474)


if __name__ == "__main__":
    unittest.main()
