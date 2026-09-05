import unittest
from types import SimpleNamespace
from unittest.mock import patch

from history_cache import HistoryCache
from market_data_hub import Bar
import stock_bar_bootstrap as stocks
import futures_bar_bootstrap as futures
from test_futures_bar_bootstrap import FakeApi, ts


def entry(count=1):
    return SimpleNamespace(bars_1m=[{}] * count, bars_5m=[])


class HistoryCacheTests(unittest.TestCase):
    def test_lru_and_bar_budget_preserve_hot_history(self):
        cache = HistoryCache(3, 6)
        cache['old'] = entry(2)
        cache['hot'] = entry(2)
        cache['other'] = entry(2)
        cache.get('hot')
        cache['new'] = entry(3)
        self.assertEqual(list(cache), ['hot', 'new'])
        cache['new'] = entry(1)
        cache['more'] = entry(2)
        self.assertEqual(len(cache), 3)
        cache['huge'] = entry(7)
        self.assertEqual(len(cache), 0)

    def test_eviction_does_not_schedule_provider_refetch_loop(self):
        with patch.object(stocks, '_history_cache', HistoryCache(1, 10)), patch.object(stocks, '_repair_targets', {'old': 1}):
            stocks._store_entry('old', entry())
            stocks._store_entry('new', entry())
            self.assertNotIn('old', stocks._repair_targets)

    def test_new_futures_session_releases_old_contract_snapshot(self):
        futures.clear_futures_bar_bootstrap_cache()
        old_key = ('TXFR1', ts(8, 45) - 86400000, 7)
        other_key = ('MXFR1', ts(8, 45), 7)
        futures._history_cache[old_key] = entry()
        futures._history_cache[other_key] = entry()
        service = SimpleNamespace(api=FakeApi(), state=SimpleNamespace(logged_in=True))
        hub = SimpleNamespace(get_live_futures_bars=lambda code: [], get_live_futures_bars_1m=lambda code: [])
        result = futures.get_resilient_futures_bars('TXFR1', service=service, hub=hub, now_ms=ts(9, 5))
        self.assertGreater(result['bar_count'], 0)
        self.assertNotIn(old_key, futures._history_cache)
        self.assertIn(other_key, futures._history_cache)
        futures.clear_futures_bar_bootstrap_cache()

    def test_compact_bars_keep_signed_force_and_price_fields(self):
        bar = Bar(ts=1, open=100, high=100, low=100, close=100)
        bar.update(102, 20, 'sell', True, 2040000)
        row = bar.to_dict()
        self.assertEqual(row['main_net_volume'], -20)
        self.assertEqual(row['main_sell_amount'], 2040000)
        self.assertEqual(row['close'], 102)
        self.assertFalse(hasattr(bar, '__dict__'))
