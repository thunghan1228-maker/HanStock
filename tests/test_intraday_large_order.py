from __future__ import annotations

import intraday_large_order as module
from intraday_large_order import IntradayLargeOrderMonitor, build_group_candidates, build_live_group_ranks


def test_builds_top_and_bottom_twenty_group_candidates():
    ranks = {group: index + 1 for index, group in enumerate(list(module.STOCK_GROUPS)[:45])}
    buy, sell = build_group_candidates(ranks)
    assert buy
    assert sell
    assert all(1 <= row["rank"] <= 20 for row in buy.values())
    assert all(1 <= row["rank"] <= 20 for row in sell.values())


def test_builds_live_group_ranks_from_local_stock_ticks():
    class Service:
        @staticmethod
        def get_stock_quote(ticker):
            return {"pct_chg": (sum(ord(char) for char in ticker) % 100 - 50) / 10}

    ranks = build_live_group_ranks(Service())

    assert len(ranks) >= 40
    assert min(ranks.values()) == 1
    assert max(ranks.values()) == len(ranks)


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


def test_persistence_lock_keeps_signal_in_memory_and_retries(monkeypatch):
    saved = []

    def locked(_rows):
        raise OSError("database locked")

    monkeypatch.setattr(module, "save_intraday_signals", locked)
    monitor = IntradayLargeOrderMonitor()
    monitor.set_candidates(
        {"2344": {"name": "華邦電", "group": "記憶體", "rank": 1, "direction": "漲幅"}},
        {},
        {"tradeDate": "2026-09-01"},
    )
    ts = 1_788_232_800_000
    emitted = monitor.on_tick({
        "code": "2344", "close": 100, "volume": 120,
        "amount": 12_000_000, "tick_type": 1,
    }, ts)

    assert len(emitted) == 1
    assert monitor.recent_signals("2026-09-01")[0]["ticker"] == "2344"
    assert monitor.status()["persistenceErrorCount"] == 1
    assert monitor.status()["pendingSignalCount"] == 1

    def recovered(rows):
        saved.extend(rows)
        return rows

    monkeypatch.setattr(module, "save_intraday_signals", recovered)
    assert monitor.flush_pending_signals() == 1
    assert saved[0]["ticker"] == "2344"
    assert monitor.status()["pendingSignalCount"] == 0


def test_candidate_refresh_recovers_missing_group_snapshot(monkeypatch):
    import group_strength_collector

    groups = list(module.STOCK_GROUPS)[:45]
    history_reads = iter([
        [],
        [{"bucketTs": 1_788_226_200_000, "ranks": {group: index + 1 for index, group in enumerate(groups)}}],
    ])
    monkeypatch.setattr(module, "load_group_strength_history", lambda _trade_date: next(history_reads))
    monkeypatch.setattr(group_strength_collector, "collect_once", lambda: True)

    class Service:
        @staticmethod
        def ensure_stock_subscriptions(codes):
            return {
                "capacity": 1000,
                "active_count": len(codes),
                "already_subscribed": codes,
                "newly_subscribed": [],
                "failed": {},
            }

    status = module.refresh_intraday_large_order_candidates(Service())

    assert status["candidateCount"] > 0
    assert status["prepared"] is True
    assert status["buyCandidateCount"] > 0
    assert status["sellCandidateCount"] > 0


def test_local_candidates_survive_snapshot_persistence_failure(monkeypatch):
    import group_strength_collector

    groups = list(module.STOCK_GROUPS)[:45]
    monkeypatch.setattr(module, "load_group_strength_history", lambda _trade_date: [])
    monkeypatch.setattr(group_strength_collector, "collect_once", lambda: False)
    monkeypatch.setattr(module, "_ensure_group_universe_subscriptions", lambda _service: None)
    monkeypatch.setattr(
        module,
        "build_live_group_ranks",
        lambda _service: {group: index + 1 for index, group in enumerate(groups)},
    )
    monkeypatch.setattr(
        module,
        "save_group_strength_snapshot",
        lambda *_args: (_ for _ in ()).throw(OSError("database locked")),
    )

    class Service:
        @staticmethod
        def ensure_stock_subscriptions(codes):
            return {
                "capacity": 1000,
                "active_count": len(codes),
                "already_subscribed": codes,
                "newly_subscribed": [],
                "failed": {},
            }

    status = module.refresh_intraday_large_order_candidates(Service())

    assert status["candidateCount"] > 0
    assert status["prepared"] is True
    assert status["candidateSource"] == "local_shioaji_group_ranking"
    assert status["snapshotPersisted"] is False
    assert status["snapshotPersistError"] == "OSError"
