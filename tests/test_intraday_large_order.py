from __future__ import annotations

import intraday_large_order as module
from intraday_large_order import IntradayLargeOrderMonitor, build_group_candidates


def test_builds_top_and_bottom_twenty_group_candidates():
    ranks = {group: index + 1 for index, group in enumerate(list(module.STOCK_GROUPS)[:45])}
    buy, sell = build_group_candidates(ranks)
    assert buy
    assert sell
    assert all(1 <= row["rank"] <= 20 for row in buy.values())
    assert all(1 <= row["rank"] <= 20 for row in sell.values())


def test_same_second_large_buy_emits_once(monkeypatch):
    inserted = []

    def fake_save(rows):
        inserted.extend(rows)
        return rows

    monkeypatch.setattr(module, "save_intraday_signals", fake_save)
    monitor = IntradayLargeOrderMonitor()
    monitor.set_candidates({"2344": {"name": "華邦電", "group": "記憶體", "rank": 4, "direction": "漲幅"}}, {}, {})
    base = 1_787_542_347_000
    result = []
    for index, lots in enumerate([360, 16, 32, 18, 10, 54, 138]):
        result.extend(monitor.on_tick({
            "code": "2344", "close": 183.5, "volume": lots,
            "amount": 183.5 * lots * 1000, "tick_type": 1,
        }, base + index * 80))
    assert len(inserted) == 1
    assert result[0]["kind"] == "instantLargeBuy"
    assert result[0]["label"] == "瞬間特大買單敲進"
    assert "同秒" in result[0]["note"]
    assert "族群同步 記憶體 漲幅第 4 名" in result[0]["note"]


def test_wrong_direction_and_neutral_ticks_are_ignored(monkeypatch):
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monitor = IntradayLargeOrderMonitor()
    monitor.set_candidates({"2344": {"name": "華邦電", "group": "記憶體", "rank": 1, "direction": "漲幅"}}, {}, {})
    assert monitor.on_tick({"code": "2344", "close": 100, "volume": 500, "amount": 50_000_000, "tick_type": 2}, 1_000_000) == []
    assert monitor.on_tick({"code": "2344", "close": 100, "volume": 500, "amount": 50_000_000, "tick_type": 0}, 1_000_100) == []
