"""Optional stock futures must never consume the equities-only quote budget."""

import os
import unittest
from unittest.mock import Mock, patch

from futures_bar_bootstrap import clear_futures_bar_bootstrap_cache, get_resilient_futures_bars
from quote_features import stock_futures_enabled
from stock_futures_service import StockFuturesQuoteService
from test_stock_futures_service import ENV, FakeApiFactory
from test_futures_bar_bootstrap import EmptyHub, FakeService, TrackingApi, ts


class EquitiesOnlyQuotesTests(unittest.TestCase):
    def test_stock_futures_default_off(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(stock_futures_enabled())
            factory = Mock(side_effect=AssertionError("No broker API may be created"))
            service = StockFuturesQuoteService(api_factory=factory)
            for mode in ("regular", "mini"):
                result = service.ensure_subscriptions(None, ["2330", "2454"], mode)
                self.assertEqual(result["status"], "disabled")
                self.assertEqual(result["newly_subscribed"], [])
                for subscribe in (True, False):
                    response = service.get_quotes(None, ["2330"], mode, subscribe=subscribe)
                    self.assertEqual(response["status"], "disabled")
                    self.assertEqual(response["data"], {})
            factory.assert_not_called()
            self.assertEqual(service.status(None)["active_subscription_count"], 0)

    @patch.dict(os.environ, {**ENV, "SHIOAJI_STOCK_FUTURES_ENABLED": "false"})
    def test_shared_stock_subscriptions_remain_available_and_receive_ticks(self):
        factory = FakeApiFactory()
        service = StockFuturesQuoteService(api_factory=factory)
        handler = Mock()
        codes = [str(1000 + i) for i in range(350)]
        result = service.ensure_stock_subscriptions(codes, handler)
        self.assertEqual(result["failed"], {})
        self.assertEqual(len(result["newly_subscribed"]), 350)
        before = sum(len(api.subscribed) for api in factory.apis)
        for mode in ("regular", "mini"):
            self.assertEqual(service.get_quotes(None, codes, mode)["status"], "disabled")
        # Stock-only requests consume every slot; old futures pages cannot take one.
        self.assertEqual(sum(len(api.subscribed) for api in factory.apis), before)
        status = service.status(None)
        self.assertFalse(status["enabled"])
        self.assertTrue(status["shared_stock_quotes_enabled"])
        self.assertEqual(status["active_stock_subscription_count"], 350)
        self.assertEqual(status["active_subscription_count"], 0)
        tick = Mock()
        factory.apis[0].stock_callback("TSE", tick)
        handler.assert_called_once_with("TSE", tick)
        service._refresh_closed_snapshots(codes, "regular")
        self.assertEqual(sum(api.snapshot_calls for api in factory.apis), 0)
        self.assertEqual(service.history_api_candidates(), [])

    @patch.dict(os.environ, {"SHIOAJI_STOCK_FUTURES_ENABLED": "false"})
    def test_old_stock_futures_charts_do_not_query_broker_or_hub(self):
        broker = Mock()
        hub = Mock()
        for interval in ("1m", "5m", "1d"):
            for code in ("NCFR1", "NCFQ6"):
                result = get_resilient_futures_bars(code, interval, service=broker, hub=hub)
                self.assertEqual(result["status"], "disabled")
                self.assertEqual(result["bars"], [])
        self.assertEqual(broker.mock_calls, [])
        self.assertEqual(hub.mock_calls, [])

    @patch.dict(os.environ, {"SHIOAJI_STOCK_FUTURES_ENABLED": "false"})
    def test_existing_market_index_chart_is_unaffected(self):
        clear_futures_bar_bootstrap_cache()
        broker = FakeService()
        broker.api = TrackingApi()
        result = get_resilient_futures_bars("TXFR1", "5m", service=broker, hub=EmptyHub(), now_ms=ts(9, 10))
        self.assertEqual(result["status"], "ok")
        self.assertGreater(broker.api.kbar_calls, 0)
        clear_futures_bar_bootstrap_cache()


if __name__ == "__main__":
    unittest.main()
