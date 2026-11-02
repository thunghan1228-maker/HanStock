from __future__ import annotations

from datetime import datetime, timezone

import gap_vwap_signals as module
from gap_vwap_signals import KIND_BEAR, KIND_BULL, GapVwapMonitor

TW_OFFSET_MS = 8 * 60 * 60 * 1000
BASE_DAY_UTC_MS = int(datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


def ts(hour: int, minute: int) -> int:
    """2026-11-02 台北時間 hour:minute 的 1 分 K 起始 ms。"""
    return BASE_DAY_UTC_MS + (hour * 60 + minute) * 60 * 1000 - TW_OFFSET_MS


def bar(hour: int, minute: int, close: float, volume: int = 100, open_: float | None = None) -> dict:
    return {"ts": ts(hour, minute), "open": open_ if open_ is not None else close, "high": close, "low": close,
            "close": close, "volume": volume}


def new_monitor(monkeypatch, prev_close: float | None = 100.0, tick=None) -> GapVwapMonitor:
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    daily = [] if prev_close is None else [
        {"ts": "2026-10-30", "open": 1, "high": 1, "low": 1, "close": prev_close, "volume": 1},
        {"ts": "2026-11-02", "open": 1, "high": 1, "low": 1, "close": 999.0, "volume": 1},  # 今天的日K不能當昨收
    ]
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=6: daily)
    return GapVwapMonitor(tick_lookup=(lambda code: tick) if tick is not None else None)


def feed(monitor: GapVwapMonitor, code: str, bars: list[dict], **kwargs) -> list[dict]:
    out = []
    for item in bars:
        out.extend(monitor.on_bar_completed(code, item, **kwargs))
    return out


def test_gap_down_then_close_1pct_above_vwap_after_0905_fires_88_once(monkeypatch):
    monitor = new_monitor(monkeypatch)
    # 開盤 98（昨收 100，開低）；09:00～09:03 收 97，09:04 收 98.5（收在 09:05，VWAP 97.3，+1.23%）。
    bars = [bar(9, 0, 97.0, 100, open_=98.0)] + [bar(9, i, 97.0, 100) for i in range(1, 4)] + [bar(9, 4, 98.5, 100), bar(9, 5, 99.0, 100)]

    signals = feed(monitor, "3532", bars)

    assert [s["kind"] for s in signals] == [KIND_BULL]
    signal = signals[0]
    assert signal["label"] == "88 開低站上VWAP"
    assert signal["barTs"] == ts(9, 5)  # 09:04 那根收在 09:05
    assert signal["price"] == 98.5
    assert signal["tradeDate"] == "2026-11-02"
    assert "開盤 98｜昨收 100（-2.00%）" in signal["note"]
    assert "VWAP 97.3｜距VWAP +1.23%" in signal["note"]


def test_before_0905_does_not_fire_even_far_above_vwap(monkeypatch):
    monitor = new_monitor(monkeypatch)
    # 09:01 那根收 99（收在 09:02）已經比 VWAP 高 1% 以上，但 09:05 前不判斷；之後回到 VWAP 附近就不發。
    bars = [bar(9, 0, 97.0, 100, open_=98.0), bar(9, 1, 99.0, 100), bar(9, 2, 98.0, 100), bar(9, 3, 98.0, 100),
            bar(9, 4, 98.0, 100), bar(9, 5, 98.0, 100)]

    assert feed(monitor, "3532", bars) == []


def test_above_vwap_but_less_than_1pct_does_not_fire(monkeypatch):
    monitor = new_monitor(monkeypatch)
    bars = [bar(9, 0, 97.0, 100, open_=98.0)] + [bar(9, i, 97.0, 100) for i in range(1, 5)] + [bar(9, 5, 97.8, 100)]

    assert feed(monitor, "3532", bars) == []


def test_gap_up_then_close_1pct_below_vwap_fires_87(monkeypatch):
    monitor = new_monitor(monkeypatch)
    bars = [bar(9, 0, 103.0, 100, open_=102.0)] + [bar(9, i, 103.0, 100) for i in range(1, 6)] + [bar(9, 6, 101.0, 100)]

    signals = feed(monitor, "3532", bars)

    assert [(s["kind"], s["label"], s["barTs"]) for s in signals] == [(KIND_BEAR, "87 開高跌破VWAP", ts(9, 7))]


def test_flat_open_never_fires(monkeypatch):
    monitor = new_monitor(monkeypatch)
    bars = [bar(9, 0, 100.0, 100, open_=100.0)] + [bar(9, i, 95.0 if i % 2 else 105.0, 100) for i in range(1, 12)]

    assert feed(monitor, "3532", bars) == []


def test_gap_down_below_vwap_all_day_does_not_fire(monkeypatch):
    monitor = new_monitor(monkeypatch)
    bars = [bar(9, 0, 99.0, 100, open_=98.0)] + [bar(9, i, 99.0 - i * 0.1, 100) for i in range(1, 10)]

    assert feed(monitor, "3532", bars) == []


def test_late_subscription_without_tick_open_cannot_judge(monkeypatch):
    monitor = new_monitor(monkeypatch)
    # 10:00 才看到第一根、沒有 tick：不知道今天開盤，不判斷。
    assert feed(monitor, "3532", [bar(10, 0, 97.0, 100, open_=97.0), bar(10, 1, 99.0, 100)]) == []


def test_tick_open_and_avg_price_are_used_for_late_subscription(monkeypatch):
    tick = {"open": 98.0, "avg_price": 97.5, "close": 99.0, "price_chg": -1.0}
    monitor = new_monitor(monkeypatch, prev_close=None, tick=tick)  # 沒有日K：昨收用 close − price_chg

    signals = feed(monitor, "3532", [bar(10, 0, 98.0), bar(10, 1, 98.5)])

    assert [s["kind"] for s in signals] == [KIND_BULL]
    assert signals[0]["barTs"] == ts(10, 2)  # 10:01 那根收 98.5，比 97.5 高 1.03%
    assert "昨收 100" in signals[0]["note"] and "VWAP 97.5" in signals[0]["note"]


def test_backfill_replays_day_bars_and_rewrites(monkeypatch):
    saved: list[dict] = []
    deleted: list[tuple] = []
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: saved.extend(rows) or rows)
    monkeypatch.setattr(module, "delete_signals_for_ticker", lambda d, c, k: deleted.append((d, c, set(k))) or 0)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=6: [])
    monkeypatch.setattr(module, "_monitor", GapVwapMonitor())
    prev_day = [{"ts": ts(13, 29) - 3 * 24 * 3600 * 1000, "open": 100.0, "high": 100.0, "low": 100.0,
                 "close": 100.0, "volume": 10}]
    history = {"bars": prev_day + [bar(9, 0, 97.0, 100, open_=98.0)] + [bar(9, i, 97.0, 100) for i in range(1, 4)] + [bar(9, 4, 98.5, 100)]}
    monkeypatch.setattr(module, "get_stock_history_bars_1m", lambda code, **kw: history)

    result = module.backfill_gap_vwap_signals("2026-11-02", codes=["3532", "NOTGROUP"])

    assert result["codeCount"] == 1 and result["processed"] == 1 and result["signalsEmitted"] == 1
    assert deleted == [("2026-11-02", "3532", {KIND_BULL, KIND_BEAR})]
    assert [s["kind"] for s in saved] == [KIND_BULL]
    assert "昨收 100" in saved[0]["note"]
