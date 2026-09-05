"""Bound decoded history in RAM; eviction never changes persisted market data.

Callers hold their existing cache locks. Per-code fetch locks remain independent
so evicting an entry cannot start two concurrent provider requests for that code.
"""
from collections import OrderedDict


class HistoryCache(OrderedDict):
    def __init__(self, max_entries: int, max_bars: int):
        super().__init__()
        self.max_entries = max_entries
        self.max_bars = max_bars

    @staticmethod
    def _bars(entry):
        return len(getattr(entry, "bars_1m", ())) + len(getattr(entry, "bars_5m", ()))

    def get(self, key, default=None):
        if key not in self:
            return default
        self.move_to_end(key)
        return super().__getitem__(key)

    def __setitem__(self, key, entry):
        super().__setitem__(key, entry)
        self.move_to_end(key)
        bars = sum(self._bars(value) for value in self.values())
        while len(self) > self.max_entries or bars > self.max_bars:
            _, removed = self.popitem(last=False)
            bars -= self._bars(removed)
