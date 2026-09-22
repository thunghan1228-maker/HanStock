from __future__ import annotations

import intraday_large_order as module
from intraday_large_order import (
    IntradayLargeOrderMonitor,
    build_group_candidates,
    build_live_group_ranks,
    normalize_intraday_large_order_signal,
)


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


def test_ensure_group_universe_subscriptions_stays_well_under_the_subscription_cap():
    # 舊bug：把每個族群(扣掉股期標的等)的全部成員一次送進
    # ensure_stock_subscriptions，加總遠超過190的訂閱上限，只有
    # STOCK_GROUPS迭代順序排在前面的族群吃得到額度、把整個族群灌滿，
    # 排在後面的族群永遠一檔都訂不到——族群涵蓋數量因此卡在
    # MIN_LIVE_GROUPS門檻之下。改成只送代表股後，總數要遠低於190，
    # 而且每個未排除的族群都至少要有一檔代表股在請求名單裡。
    requested: list[str] = []

    class Service:
        @staticmethod
        def ensure_stock_subscriptions(codes):
            requested.extend(codes)
            return {"failed": {}}

    module._ensure_group_universe_subscriptions(Service())

    assert 0 < len(requested) < 190
    requested_set = set(requested)
    non_excluded_groups = [g for g in module.STOCK_GROUPS if g not in module.EXCLUDED_GROUPS]
    assert len(non_excluded_groups) > 40  # 確認這個測試環境本身就是會踩到舊bug的規模
    for group in non_excluded_groups:
        members = module.STOCK_GROUPS[group]
        assert any(ticker in requested_set for ticker, _name in members), f"{group} 沒有任何代表股被訂閱"


def test_same_second_large_buy_emits_once(monkeypatch):
    inserted = []

    def fake_save(rows):
        inserted.extend(rows)
        return rows

    monkeypatch.setattr(module, "save_intraday_signals", fake_save)
    monkeypatch.setattr(module, "_holder_strength_pct", lambda code: 15.0)
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


def test_buy_and_sell_amount_thresholds_are_thirty_and_fifty_million(monkeypatch):
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    assert module.MIN_BURST_AMOUNT == 30_000_000
    assert module.EXTRA_BURST_AMOUNT == 50_000_000

    cases = [
        (1, "buy", 29_999_999, None),
        (1, "buy", 30_000_000, "瞬間大單連續敲進"),
        (1, "buy", 50_000_000, "瞬間特大買單敲進"),
        (2, "sell", 29_999_999, None),
        (2, "sell", 30_000_000, "瞬間大單連續賣出"),
        (2, "sell", 50_000_000, "瞬間特大賣單倒出"),
    ]
    for tick_type, side, amount, expected_label in cases:
        monkeypatch.setattr(module, "_holder_strength_pct", lambda code, side=side: (1.0 if side == "buy" else -1.0))
        monitor = IntradayLargeOrderMonitor()
        candidate = {"2344": {"name": "華邦電", "group": "記憶體", "rank": 4, "direction": "漲幅" if side == "buy" else "跌幅"}}
        monitor.set_candidates(candidate if side == "buy" else {}, candidate if side == "sell" else {}, {})
        result = monitor.on_tick({
            "code": "2344", "close": 100, "volume": 1,
            "amount": amount, "tick_type": tick_type,
        }, 1_787_542_347_000)
        if expected_label is None:
            assert result == []
        else:
            assert result[0]["label"] == expected_label


def test_saved_signals_are_rechecked_against_current_thresholds():
    def saved(ticker, kind, label, note):
        return {"tradeDate": "2026-09-01", "ticker": ticker, "kind": kind, "label": label, "note": note}

    assert normalize_intraday_large_order_signal(saved(
        "8027", "instantLargeBuy", "瞬間大單連續敲進", "同秒 2 筆｜合計 65 張｜約 1229.5 萬",
    )) is None
    assert normalize_intraday_large_order_signal(saved(
        "3443", "instantLargeBuy", "瞬間大單連續敲進", "同秒 1 筆｜合計 2 張｜約 1212.0 萬",
    )) is None
    assert normalize_intraday_large_order_signal(saved(
        "2615", "instantLargeSell", "瞬間大單連續賣出", "同秒 1 筆｜合計 100 張｜約 1145.0 萬",
    ))["label"] == "瞬間大單連續賣出"
    assert normalize_intraday_large_order_signal(saved(
        "4991", "instantLargeBuy", "瞬間大單連續敲進", "同秒 3 筆｜合計 66 張｜約 3618.2 萬",
    ))["label"] == "瞬間大單連續敲進"


def test_saved_extra_signal_is_downgraded_when_only_general_threshold_passes():
    normalized = normalize_intraday_large_order_signal({
        "tradeDate": "2026-09-01",
        "ticker": "1234",
        "kind": "instantLargeSell",
        "label": "瞬間特大賣單倒出",
        "note": "同秒 1 筆｜合計 59 張｜約 3073.9 萬",
    })

    assert normalized is not None
    assert normalized["label"] == "瞬間大單連續賣出"


def test_holder_strength_wrong_direction_blocks_signal(monkeypatch):
    # 買方大單累計已達門檻，但觸發當時大戶力是負的（偏賣），不成立。
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "_holder_strength_pct", lambda code: -5.0)
    monitor = IntradayLargeOrderMonitor()
    monitor.set_candidates({"2344": {"name": "華邦電", "group": "記憶體", "rank": 4, "direction": "漲幅"}}, {}, {})
    result = monitor.on_tick({
        "code": "2344", "close": 100, "volume": 1,
        "amount": 50_000_000, "tick_type": 1,
    }, 1_787_542_347_000)
    assert result == []


def test_holder_strength_missing_data_blocks_signal(monkeypatch):
    # 大戶力算不出來（例如今天還沒有累計成交額資料）時保守地不發訊號。
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "_holder_strength_pct", lambda code: None)
    monitor = IntradayLargeOrderMonitor()
    monitor.set_candidates({"2344": {"name": "華邦電", "group": "記憶體", "rank": 4, "direction": "漲幅"}}, {}, {})
    result = monitor.on_tick({
        "code": "2344", "close": 100, "volume": 1,
        "amount": 50_000_000, "tick_type": 1,
    }, 1_787_542_347_000)
    assert result == []


def test_holder_strength_rejection_does_not_consume_cooldown(monkeypatch):
    # 被大戶力方向擋掉的那一筆，不能佔用冷卻時間；换成正確方向後應該還能觸發。
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monitor = IntradayLargeOrderMonitor()
    monitor.set_candidates({"2344": {"name": "華邦電", "group": "記憶體", "rank": 4, "direction": "漲幅"}}, {}, {})

    monkeypatch.setattr(module, "_holder_strength_pct", lambda code: -5.0)
    rejected = monitor.on_tick({
        "code": "2344", "close": 100, "volume": 1,
        "amount": 50_000_000, "tick_type": 1,
    }, 1_787_542_347_000)
    assert rejected == []

    monkeypatch.setattr(module, "_holder_strength_pct", lambda code: 5.0)
    accepted = monitor.on_tick({
        "code": "2344", "close": 100, "volume": 1,
        "amount": 50_000_000, "tick_type": 1,
    }, 1_787_542_347_100)
    assert len(accepted) == 1


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
    monkeypatch.setattr(module, "_holder_strength_pct", lambda code: 20.0)
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


def test_local_candidates_survive_snapshot_persistence_failure(monkeypatch):
    groups = list(module.STOCK_GROUPS)[:45]
    monkeypatch.setattr(module, "load_group_strength_history", lambda _trade_date: [])
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
