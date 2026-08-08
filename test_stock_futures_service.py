from __future__ import annotations

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from stock_futures_service import (
    TW_TZ,
    StockFuturesQuoteService,
    is_stock_futures_day_session,
    resolve_front_month_contract,
)


class Contract(SimpleNamespace):
    pass


class FakeQuoteEvents:
    def __init__(self):
        self.callback = None

    def on_event(self, callback):
        self.callback = callback
        return callback


class FakeContracts:
    def __init__(self):
        self.targets: dict[tuple[str, str], str] = {}

    def get(self, code):
        code = str(code).strip().upper()
        return Contract(code=code, security_type="STK", name=f"股票{code}") if code else None

    def futures_by_underlying(self, stock):
        code = stock.code
        regular_target = self.targets.get(("regular", code), f"R{code}H6")
        mini_target = self.targets.get(("mini", code), f"M{code}H6")
        return [
            Contract(
                code=f"R{code}R1",
                target_code=regular_target,
                name=f"股票{code}期貨近月",
                root=f"R{code}",
                underlying_code=code,
                delivery_month="202608" if regular_target.endswith("H6") else "202609",
                delivery_date="2026/08/19" if regular_target.endswith("H6") else "2026/09/16",
                contract_size=2000,
            ),
            # 刻意不在名稱寫「小型」；用 contract_size=100 驗證 mini 判斷，
            # 可涵蓋個別 Shioaji 商品名稱不一致的情況。
            Contract(
                code=f"M{code}R1",
                target_code=mini_target,
                name=f"股票{code}期貨100股近月",
                root=f"M{code}",
                underlying_code=code,
                delivery_month="202608" if mini_target.endswith("H6") else "202609",
                delivery_date="2026/08/19" if mini_target.endswith("H6") else "2026/09/16",
                contract_size=100,
            ),
        ]


class FakeApi:
    def __init__(self, index: int):
        self.index = index
        self.contracts = FakeContracts()
        self.quote = FakeQuoteEvents()
        self.subscribed = []
        self.unsubscribed = []
        self.callback = None
        self.logged_in = False
        self.logged_out = False

    def login(self, api_key=None, secret_key=None):
        if not api_key or not secret_key:
            raise RuntimeError("missing fake credentials")
        self.logged_in = True
        return []

    def logout(self):
        self.logged_out = True
        self.logged_in = False

    def set_on_quote_fop_v1_callback(self, callback):
        self.callback = callback

    def subscribe(self, contract, quote_type=None):
        self.subscribed.append((contract, quote_type))

    def unsubscribe(self, contract, quote_type=None):
        self.unsubscribed.append((contract, quote_type))


class FakeApiFactory:
    def __init__(self):
        self.apis: list[FakeApi] = []

    def __call__(self):
        api = FakeApi(len(self.apis))
        self.apis.append(api)
        return api


class FakeQuote(SimpleNamespace):
    pass


ENV = {
    "SHIOAJI_API_KEY": "fake-key",
    "SHIOAJI_SECRET_KEY": "fake-secret",
    "SHIOAJI_STOCK_FUTURES_POOL_SIZE": "2",
    "SHIOAJI_STOCK_FUTURES_PER_CONNECTION_CAP": "180",
    "SHIOAJI_STOCK_FUTURES_R1_RECHECK_SECONDS": "300",
}


class StockFuturesServiceTests(unittest.TestCase):
    def test_session_boundaries(self):
        self.assertFalse(is_stock_futures_day_session(datetime(2026, 8, 7, 8, 44, 59, tzinfo=TW_TZ)))
        self.assertTrue(is_stock_futures_day_session(datetime(2026, 8, 7, 8, 45, 0, tzinfo=TW_TZ)))
        self.assertTrue(is_stock_futures_day_session(datetime(2026, 8, 7, 13, 45, 0, tzinfo=TW_TZ)))
        self.assertFalse(is_stock_futures_day_session(datetime(2026, 8, 7, 13, 45, 1, tzinfo=TW_TZ)))
        self.assertFalse(is_stock_futures_day_session(datetime(2026, 8, 8, 10, 0, 0, tzinfo=TW_TZ)))

    def test_resolver_separates_regular_and_mini_r1_by_contract_size(self):
        api = FakeApi(0)
        regular = resolve_front_month_contract(api, "2330", "regular")
        mini = resolve_front_month_contract(api, "2330", "mini")
        self.assertEqual(regular.code, "R2330R1")
        self.assertEqual(regular.target_code, "R2330H6")
        self.assertEqual(mini.code, "M2330R1")
        self.assertEqual(mini.target_code, "M2330H6")
        self.assertEqual(mini.contract_size, 100)

    @patch.dict(os.environ, ENV, clear=False)
    def test_294_futures_are_balanced_across_two_dedicated_connections(self):
        factory = FakeApiFactory()
        service = StockFuturesQuoteService(api_factory=factory)
        regular_codes = [f"{1000 + i:04d}" for i in range(247)]
        mini_codes = [f"{5000 + i:04d}" for i in range(47)]

        regular = service.ensure_subscriptions(None, regular_codes, "regular")
        mini = service.ensure_subscriptions(None, mini_codes, "mini")

        self.assertEqual(regular["failed"], {})
        self.assertEqual(mini["failed"], {})
        self.assertEqual(len(regular["newly_subscribed"]), 247)
        self.assertEqual(len(mini["newly_subscribed"]), 47)
        self.assertEqual(len(factory.apis), 2)

        status = service.status(None)
        counts = list(status["pool_counts"].values())
        self.assertEqual(sum(counts), 294)
        self.assertLessEqual(max(counts), 180)
        self.assertLessEqual(max(counts) - min(counts), 1)
        self.assertTrue(status["enabled"])

    @patch.dict(os.environ, ENV, clear=False)
    def test_quote_callback_routes_by_pool_and_returns_futures_not_spot(self):
        factory = FakeApiFactory()
        service = StockFuturesQuoteService(api_factory=factory)
        result = service.ensure_subscriptions(None, ["2330"], "mini")
        self.assertEqual(result["failed"], {})
        self.assertEqual(result["newly_subscribed"], ["2330"])

        status = service.status(None)
        mapping = status["mappings"]["mini:2330"]
        pool_index = mapping["pool_index"]
        target_code = mapping["target_code"]
        self.assertEqual(target_code, "M2330H6")

        quote = FakeQuote(
            code=target_code,
            datetime=datetime(2026, 8, 7, 9, 0, tzinfo=TW_TZ),
            close=1000,
            open=990,
            high=1005,
            low=985,
            avg_price=997,
            price_chg=20,
            pct_chg=2.040816,
            volume=3,
            total_volume=123,
            amount=3000,
            total_amount=123000,
            bid_side_total_vol=50,
            ask_side_total_vol=40,
            simtrade=False,
        )
        handled = service.on_quote(pool_index, "TAIFEX", quote)
        self.assertTrue(handled)
        payload = service.get_quotes(None, ["2330"], "mini", subscribe=False)
        row = payload["data"]["2330"]
        self.assertEqual(row["futures_code"], "M2330R1")
        self.assertEqual(row["target_code"], "M2330H6")
        self.assertEqual(row["close"], 1000.0)
        self.assertAlmostEqual(row["pct_chg"], 0.02040816)
        self.assertAlmostEqual(row["pct_chg_pct"], 2.040816)
        self.assertEqual(row["data_source"], "shioaji_realtime_stock_futures")

    @patch.dict(os.environ, ENV, clear=False)
    def test_r1_target_change_auto_rolls_without_hard_coded_month(self):
        factory = FakeApiFactory()
        service = StockFuturesQuoteService(api_factory=factory)
        first = service.ensure_subscriptions(None, ["2330"], "regular")
        self.assertEqual(first["newly_subscribed"], ["2330"])
        self.assertEqual(service.status(None)["mappings"]["regular:2330"]["target_code"], "R2330H6")

        # 模擬 Shioaji 合約下載在換月後把 R1 target 指到下一個近月。
        for api in factory.apis:
            api.contracts.targets[("regular", "2330")] = "R2330I6"
        service._recheck_seconds = 0

        second = service.ensure_subscriptions(None, ["2330"], "regular")
        self.assertEqual(len(second["rolled"]), 1)
        self.assertEqual(second["rolled"][0]["from"], "R2330H6")
        self.assertEqual(second["rolled"][0]["to"], "R2330I6")
        self.assertEqual(service.status(None)["mappings"]["regular:2330"]["target_code"], "R2330I6")
        self.assertGreaterEqual(len(factory.apis[0].unsubscribed), 1)

    @patch.dict(os.environ, ENV, clear=False)
    def test_shutdown_logs_out_dedicated_pool_connections(self):
        factory = FakeApiFactory()
        service = StockFuturesQuoteService(api_factory=factory)
        service.ensure_subscriptions(None, ["2330"], "regular")
        self.assertEqual(len(factory.apis), 2)
        service.shutdown()
        self.assertTrue(all(api.logged_out for api in factory.apis))


if __name__ == "__main__":
    unittest.main()
