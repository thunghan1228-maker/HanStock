from __future__ import annotations

import importlib.util
import os
import sys
import threading
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
        os.environ["SHIOAJI_SHARED_POOL_LOGIN_SPACING_SECONDS"] = "0"
        os.environ.pop("SHIOAJI_SHARED_STOCK_POOL_ENABLED", None)
        os.environ["SHIOAJI_API_KEY"] = "fake-key"
        os.environ["SHIOAJI_SECRET_KEY"] = "fake-secret"
        import stock_futures_service

        stock_futures_service._service = None
        # 其他測試可能先用真的 shioaji 載入過 stock_futures_service：共享連線一律用假的 API，不真的登入
        self._sj_patch = patch.object(stock_futures_service, "sj", fake_sj)
        self._sj_patch.start()
        self.service = module.QuoteService()
        self.service.api = FakeAPI()
        self.service.state.logged_in = True

    def test_history_error_preserves_fresh_primary_ticks(self):
        self.service.state.last_quote_timestamp = time.time()
        self.service.state.quote_connected = True
        self.service.state.subscribed = True
        with patch.object(self.service, "_trigger_reconnect") as reconnect:
            self.assertFalse(self.service.recover_transient_p2p_session("Kbars NotReady"))
            reconnect.assert_not_called()
        self.assertTrue(self.service.state.quote_connected)
        self.assertTrue(self.service.state.subscribed)

    def test_stale_recovery_requests_have_a_cooldown(self):
        self.service.state.last_quote_timestamp = time.time() - 180
        with patch.object(self.service, "_trigger_reconnect") as reconnect:
            self.assertTrue(self.service.recover_transient_p2p_session("stale"))
            self.assertFalse(self.service.recover_transient_p2p_session("another history failure"))
            self.assertEqual(reconnect.call_count, 1)

    def test_concurrent_startup_logs_in_once(self):
        self.service.state.logged_in = False
        calls = []
        def start():
            calls.append(1)
            time.sleep(0.02)
            self.service.state.logged_in = True
        with patch.object(self.service, "_startup_once", side_effect=start):
            with ThreadPoolExecutor(max_workers=16) as pool:
                list(pool.map(lambda _: self.service.startup(), range(32)))
        self.assertEqual(len(calls), 1)

    def test_concurrent_reconnect_callbacks_launch_one_worker(self):
        created = []
        class SlowStartThread:
            def __init__(self, **kwargs):
                self.alive = False
                created.append(self)
            def is_alive(self):
                return self.alive
            def start(self):
                time.sleep(0.02)
                self.alive = True
        callers = [threading.Thread(target=self.service._trigger_reconnect) for _ in range(24)]
        with patch.object(module.threading, "Thread", SlowStartThread):
            for caller in callers:
                caller.start()
            for caller in callers:
                caller.join(timeout=2)
                self.assertFalse(caller.is_alive())
        self.assertEqual(len(created), 1)

    def test_broker_login_throttle_waits_at_least_one_minute(self):
        self.service.state.quote_connected = False
        self.service.state.error_message = "StatusCode: 503, 請1分鐘後再重新登入"
        with patch.object(self.service._shutdown_event, "wait", return_value=True) as wait:
            self.service._reconnect_loop()
        wait.assert_called_once_with(timeout=60)

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
        self._sj_patch.stop()

    def test_stock_tick_normalizes_pct_chg(self):
        data = self.service._stock_tick_to_dict(FakeExchange.TSE, FakeTick())
        self.assertEqual(data["code"], "2330")
        self.assertEqual(data["pct_chg"], 1.11)
        self.assertIn("2026-08-04T09:12:30.123456+08:00", data["tick_time"])

    def test_shared_stock_tick_does_not_mask_stale_primary_connection(self):
        """共享池仍有現貨 Tick 時，不可讓主連線重連誤判為已恢復。"""
        self.service.state.quote_connected = False
        fake_hub = types.SimpleNamespace(on_stock_tick=lambda _tick: None)

        with patch.object(module, "get_market_data_hub", return_value=fake_hub):
            self.service._handle_stock_tick(FakeExchange.TSE, FakeTick())

        self.assertFalse(self.service.state.quote_connected)
        self.assertIsNotNone(self.service.get_stock_quote("2330"))

    def test_primary_stock_tick_refreshes_primary_connection_health(self):
        """主連線現貨 Tick 可證明 Session 健康，不必等待台指期成交。"""
        self.service.state.quote_connected = False
        self.service.state.last_quote_timestamp = None
        fake_hub = types.SimpleNamespace(on_stock_tick=lambda _tick: None)

        with patch.object(module, "get_market_data_hub", return_value=fake_hub):
            self.service._handle_stock_tick(
                FakeExchange.TSE,
                FakeTick(),
                primary_connection=True,
            )

        self.assertTrue(self.service.state.quote_connected)
        self.assertIsNotNone(self.service.state.last_quote_timestamp)

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

    @patch.dict(os.environ, {"SHIOAJI_SHARED_STOCK_POOL_ENABLED": "false"}, clear=False)
    def test_capacity_full_fails_new_codes_without_eviction_when_shared_pool_is_off(self):
        # 共享連線關掉：主連線滿了，新的股票直接回「台股訂閱上限」，不踢掉舊的。
        self.service.ensure_stock_subscriptions(["2330", "2344"])
        result = self.service.ensure_stock_subscriptions(["2408"])
        self.assertEqual(result["newly_subscribed"], [])
        self.assertEqual(result["failed"], {"2408": "台股訂閱上限"})
        self.assertEqual(result["evicted"], [])
        self.assertEqual(result["active_count"], 2)
        self.assertNotIn(("2330", "tick"), self.service.api.unsubscribed)
        self.assertEqual(self.service.get_active_stock_codes(), ["2330", "2344"])

    def test_main_full_overflows_to_shared_pool_without_eviction(self):
        # 2026-10-13 使用者：盤中 499 檔都要有逐筆——主連線滿了改訂在共享連線上，舊的不踢掉。
        self.service.ensure_stock_subscriptions(["2330", "2344"])
        result = self.service.ensure_stock_subscriptions(["2408"])
        self.assertEqual(result["newly_subscribed"], ["2408"])
        self.assertEqual(result["failed"], {})
        self.assertEqual(result["evicted"], [])
        self.assertEqual(result["main_active_count"], 2)
        self.assertEqual(result["active_count"], 3)
        self.assertNotIn(("2408", "tick"), self.service.api.subscribed)   # 沒訂到主連線
        self.assertEqual(self.service.get_active_stock_codes(), ["2330", "2344", "2408"])   # 主力副圖收集器看得到
        health = self.service.get_stock_health()
        self.assertEqual(health["main_connection_active_count"], 2)
        self.assertEqual(health["shared_pool_active_count"], 1)
        self.assertEqual(health["active_subscription_count"], 3)
        self.assertEqual(health["shared_pool_capacity"], 200)   # 2 條 × 100
        again = self.service.ensure_stock_subscriptions(["2408"])
        self.assertEqual(again["already_subscribed"], ["2408"])
        self.assertEqual(again["newly_subscribed"], [])

    def test_code_already_on_shared_pool_is_not_subscribed_again_on_main(self):
        # 主連線後來有空位（例如重連），已經在共享連線上的股票不能再訂一次到主連線，不然同一筆成交會算兩次。
        self.service.ensure_stock_subscriptions(["2330", "2344", "2408"])
        self.service._unsubscribe_stock("2344")
        result = self.service.ensure_stock_subscriptions(["2408"])
        self.assertEqual(result["already_subscribed"], ["2408"])
        self.assertNotIn(("2408", "tick"), self.service.api.subscribed)

    def test_shared_pool_ticks_update_quotes_without_marking_primary_healthy(self):
        self.service.state.quote_connected = False
        fake_hub = types.SimpleNamespace(ticks=[], on_stock_tick=lambda tick: fake_hub.ticks.append(tick["code"]))
        with patch.object(module, "get_market_data_hub", return_value=fake_hub):
            self.service._handle_shared_stock_tick(FakeExchange.TSE, FakeTick())
        self.assertEqual(fake_hub.ticks, ["2330"])
        self.assertIsNotNone(self.service.get_stock_quote("2330"))
        self.assertFalse(self.service.state.quote_connected)

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

        failed = {code: reason for result in results for code, reason in result["failed"].items()}
        self.assertEqual(failed, {})
        health = self.service.get_stock_health()
        self.assertEqual(health["main_connection_active_count"], 2)
        self.assertEqual(health["shared_pool_active_count"], 2)
        self.assertEqual(health["active_subscription_count"], 4)
        self.assertEqual(len(self.service.api.subscribed), 2)

    def test_base_contract_is_used_for_subscriptions_and_contracts_stocks_only_as_fallback(self):
        # Shioaji 1.7：api.contracts.get 回 BaseContract（登入後馬上有、成本趨近零），訂閱用它就夠；
        # 只有 get 拿不到時才試 Contracts.Stocks[code]，成本未知的路不放在訂閱路徑上。
        service = self.service

        @dataclass
        class FullStock:
            code: str
            security_type: str = "STK"
            day_trade: str = "Yes"
            margin_trading_balance: int = 1
            short_selling_balance: int = 1

        class Stocks:
            def __init__(self):
                self.calls = []

            def __getitem__(self, code):
                self.calls.append(code)
                if code != "BAD1":
                    raise KeyError(code)
                return FullStock(code)

        stocks = Stocks()
        service.api.Contracts = types.SimpleNamespace(Stocks=stocks)
        self.assertIsInstance(service._resolve_stock_contract("2330"), FakeContract)
        self.assertEqual(stocks.calls, [])
        self.assertIsInstance(service._resolve_stock_contract("BAD1"), FullStock)  # get 回 None → 退到 Contracts.Stocks
        self.assertIsNone(service._resolve_stock_contract("BAD2"))
        self.assertTrue(service.ensure_stock_subscriptions(["2330"]))
        self.assertIn(("2330", FakeQuoteType.Tick), service.api.subscribed)

    def test_invalid_contract_returns_failure(self):
        result = self.service.ensure_stock_subscriptions(["BAD1"])
        self.assertIn("BAD1", result["failed"])

    def _universe_service(self):
        import stock_futures_service

        if stock_futures_service._service is not None:
            stock_futures_service._service.shutdown()
        stock_futures_service._service = None
        service = module.QuoteService()
        service.api = FakeAPI()
        service.state.logged_in = True
        return service

    @patch.dict(
        os.environ,
        {
            "SHIOAJI_MAIN_STOCK_MAX_SUBSCRIPTIONS": "190",
            "SHIOAJI_SHARED_QUOTE_POOL_SIZE": "2",
            "SHIOAJI_SHARED_PER_CONNECTION_CAP": "198",
        },
        clear=False,
    )
    def test_full_499_stock_universe_fits_main_plus_two_shared_connections(self):
        service = self._universe_service()
        codes = [f"{1000 + index:04d}" for index in range(499)]

        result = service.ensure_stock_subscriptions(codes)

        self.assertEqual(result["failed"], {})
        self.assertEqual(sorted(result["newly_subscribed"]), codes)
        self.assertEqual(result["evicted"], [])
        self.assertEqual(result["main_active_count"], 190)
        self.assertEqual(result["active_count"], 499)
        self.assertEqual(sorted(service.get_active_stock_codes()), codes)
        health = service.get_stock_health()
        self.assertEqual(health["main_connection_active_count"], 190)
        self.assertEqual(health["shared_pool_active_count"], 309)
        self.assertEqual(health["shared_pool"]["pool_count"], 2)
        self.assertEqual(sorted(health["shared_pool"]["pool_counts"].values()), [154, 155])   # 平均分到兩條
        self.assertEqual(health["eviction_policy"], "disabled")

    @patch.dict(
        os.environ,
        {
            "SHIOAJI_MAIN_STOCK_MAX_SUBSCRIPTIONS": "190",
            "SHIOAJI_SHARED_QUOTE_POOL_SIZE": "2",
            "SHIOAJI_SHARED_PER_CONNECTION_CAP": "198",
            "SHIOAJI_SHARED_STOCK_POOL_ENABLED": "false",
        },
        clear=False,
    )
    def test_full_universe_caps_at_main_limit_when_shared_pool_is_off(self):
        service = self._universe_service()
        codes = [f"{1000 + index:04d}" for index in range(499)]

        result = service.ensure_stock_subscriptions(codes)

        self.assertEqual(result["newly_subscribed"], codes[:190])
        self.assertEqual(result["failed"], {code: "台股訂閱上限" for code in codes[190:]})
        self.assertEqual(len(service.get_active_stock_codes()), 190)
        health = service.get_stock_health()
        self.assertEqual(health["shared_pool_active_count"], 0)

    def test_shutdown_logs_out_shared_connections(self):
        import stock_futures_service

        self.service.ensure_stock_subscriptions(["2330", "2344", "2408"])
        pools = list(stock_futures_service._service._pools)
        self.assertTrue(pools)
        self.service.shutdown()
        self.assertTrue(all(pool.api.logged_out for pool in pools))
        self.assertIsNone(stock_futures_service._service)


if __name__ == "__main__":
    unittest.main()
