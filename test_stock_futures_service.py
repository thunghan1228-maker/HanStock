from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from stock_futures_service import (
    TW_TZ,
    StockFuturesQuoteService,
    is_stock_futures_day_session,
    resolve_front_month_contract,
)


class Contract(SimpleNamespace):
    pass


class FakeContracts:
    def __init__(self):
        self.stock = Contract(code="2330", security_type="STK", name="台積電")
        self.rows = [
            Contract(code="CDFH6", target_code="CDFH6", name="台積電期貨 202608", root="CDF", underlying_code="2330", delivery_month="202608", delivery_date="2026/08/19"),
            Contract(code="CDFR1", target_code="CDFH6", name="台積電期貨 近月", root="CDF", underlying_code="2330", delivery_month="202608", delivery_date="2026/08/19"),
            Contract(code="QFFH6", target_code="QFFH6", name="小型台積電期貨 202608", root="QFF", underlying_code="2330", delivery_month="202608", delivery_date="2026/08/19"),
            Contract(code="QFFR1", target_code="QFFH6", name="小型台積電期貨 近月", root="QFF", underlying_code="2330", delivery_month="202608", delivery_date="2026/08/19"),
        ]

    def get(self, code):
        return self.stock if code == "2330" else None

    def futures_by_underlying(self, contract):
        return list(self.rows)


class FakeApi:
    def __init__(self):
        self.contracts = FakeContracts()
        self.subscribed = []
        self.unsubscribed = []
        self.callback = None

    def set_on_quote_fop_v1_callback(self, callback):
        self.callback = callback

    def subscribe(self, contract, quote_type=None):
        self.subscribed.append((contract, quote_type))

    def unsubscribe(self, contract, quote_type=None):
        self.unsubscribed.append((contract, quote_type))


class FakeQuoteService:
    def __init__(self):
        self.api = FakeApi()
        self.state = SimpleNamespace(logged_in=True)
        self.spot = []
        self.evicted_spot = []

    def get_active_stock_codes(self):
        return list(self.spot)

    def _unsubscribe_stock(self, code):
        if code in self.spot:
            self.spot.remove(code)
            self.evicted_spot.append(code)


class FakeQuote(SimpleNamespace):
    pass


class StockFuturesServiceTests(unittest.TestCase):
    def test_session_boundaries(self):
        self.assertFalse(is_stock_futures_day_session(datetime(2026, 8, 7, 8, 44, 59, tzinfo=TW_TZ)))
        self.assertTrue(is_stock_futures_day_session(datetime(2026, 8, 7, 8, 45, 0, tzinfo=TW_TZ)))
        self.assertTrue(is_stock_futures_day_session(datetime(2026, 8, 7, 13, 45, 0, tzinfo=TW_TZ)))
        self.assertFalse(is_stock_futures_day_session(datetime(2026, 8, 7, 13, 45, 1, tzinfo=TW_TZ)))
        self.assertFalse(is_stock_futures_day_session(datetime(2026, 8, 8, 10, 0, 0, tzinfo=TW_TZ)))

    def test_resolver_separates_regular_and_mini_r1(self):
        api = FakeApi()
        regular = resolve_front_month_contract(api, "2330", "regular")
        mini = resolve_front_month_contract(api, "2330", "mini")
        self.assertEqual(regular.code, "CDFR1")
        self.assertEqual(regular.target_code, "CDFH6")
        self.assertEqual(mini.code, "QFFR1")
        self.assertEqual(mini.target_code, "QFFH6")

    def test_ensure_subscribe_uses_quote_callback_and_maps_target_code(self):
        service = StockFuturesQuoteService()
        quote_service = FakeQuoteService()
        result = service.ensure_subscriptions(quote_service, ["2330"], "mini")
        self.assertEqual(result["failed"], {})
        self.assertEqual(result["newly_subscribed"], ["2330"])
        self.assertIsNotNone(quote_service.api.callback)
        self.assertEqual(quote_service.api.subscribed[-1][0].code, "QFFR1")

        quote = FakeQuote(
            code="QFFH6",
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
        handled = service.on_quote("TAIFEX", quote)
        self.assertTrue(handled)
        payload = service.get_quotes(quote_service, ["2330"], "mini", subscribe=False)
        row = payload["data"]["2330"]
        self.assertEqual(row["futures_code"], "QFFR1")
        self.assertEqual(row["target_code"], "QFFH6")
        self.assertEqual(row["close"], 1000.0)
        self.assertAlmostEqual(row["pct_chg"], 0.02040816)
        self.assertAlmostEqual(row["pct_chg_pct"], 2.040816)

    def test_r1_target_change_auto_rolls_without_hard_coded_month(self):
        service = StockFuturesQuoteService()
        quote_service = FakeQuoteService()
        first = service.ensure_subscriptions(quote_service, ["2330"], "regular")
        self.assertEqual(first["newly_subscribed"], ["2330"])
        old_contract = quote_service.api.subscribed[-1][0]
        self.assertEqual(old_contract.target_code, "CDFH6")

        for row in quote_service.api.contracts.rows:
            if row.code == "CDFR1":
                row.target_code = "CDFI6"
                row.delivery_month = "202609"
                row.delivery_date = "2026/09/16"

        second = service.ensure_subscriptions(quote_service, ["2330"], "regular")
        self.assertEqual(len(second["rolled"]), 1)
        self.assertEqual(second["rolled"][0]["from"], "CDFH6")
        self.assertEqual(second["rolled"][0]["to"], "CDFI6")
        self.assertEqual(quote_service.api.unsubscribed[-1][0].code, "CDFR1")
        self.assertEqual(quote_service.api.subscribed[-1][0].target_code, "CDFI6")


if __name__ == "__main__":
    unittest.main()
