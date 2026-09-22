from __future__ import annotations

from datetime import datetime, timezone

import main_force_flip_signals as module
from main_force_flip_signals import KIND_BEAR, KIND_BULL, MainForceFlipMonitor

TW_OFFSET_MS = 8 * 60 * 60 * 1000
BASE_DAY_UTC_MS = int(datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


def ts(hour: int, minute: int) -> int:
    """2026-09-18 台北時間 hour:minute 的 1 分 K 起始 ms。"""
    return BASE_DAY_UTC_MS + (hour * 60 + minute) * 60 * 1000 - TW_OFFSET_MS


def bar(hour: int, minute: int, close: float, volume: int, main_buy: int = 0, main_sell: int = 0, **extra) -> dict:
    return {
        "ts": ts(hour, minute), "open": close, "high": close, "low": close, "close": close,
        "volume": volume, "main_buy_volume": main_buy, "main_sell_volume": main_sell, **extra,
    }


def new_monitor(monkeypatch, avg_daily_volume: float | None = 1000.0) -> MainForceFlipMonitor:
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    daily = []
    if avg_daily_volume is not None:
        daily = [{"ts": f"2026-09-{10 + i:02d}", "open": 100.0, "high": 100.0, "low": 100.0,
                  "close": 100.0, "volume": int(avg_daily_volume)} for i in range(5)]
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=6: daily)
    return MainForceFlipMonitor()


def feed(monitor: MainForceFlipMonitor, code: str, bars: list[dict]) -> list[dict]:
    out = []
    for item in bars:
        out.extend(monitor.on_bar_completed(code, item))
    return out


def test_strong_bull_flip_fires_once_when_zero_axis_and_vwap_cross_together(monkeypatch):
    # 使用者另一台工具實際發出的「主力累計強勢翻多」情境：前面主力偏賣、VWAP之下，
    # 第10分鐘主力大單敲進把累計淨額翻正、收盤同時站上VWAP，淨額率跟量比都很高。
    monitor = new_monitor(monkeypatch)
    quiet = [bar(9, i, 100.0, 100, main_sell=10) for i in range(9)]
    flip = bar(9, 9, 101.0, 100, main_buy=400)

    signals = feed(monitor, "3532", quiet + [flip])

    assert [s["kind"] for s in signals] == [KIND_BULL]
    signal = signals[0]
    assert signal["label"] == "主力累計強勢翻多"
    assert signal["price"] == 101.0
    assert signal["barTs"] == ts(9, 10)
    assert signal["groupName"] == "矽晶圓"
    assert signal["name"] == "台勝科"
    assert "主力零軸 09:10" in signal["note"]
    assert "VWAP穿越 09:10" in signal["note"]
    assert "累計 +310 張" in signal["note"]
    assert "量比 27.00×" in signal["note"]
    assert len(signal["note"]) <= 160

    # 同一天同方向只發一次。
    again = feed(monitor, "3532", [bar(9, 10, 102.0, 100, main_buy=100), bar(9, 11, 103.0, 100, main_buy=100)])
    assert again == []


def test_weak_volume_ratio_downgrades_label_to_plain_flip(monkeypatch):
    monitor = new_monitor(monkeypatch)
    quiet = [bar(9, i, 100.0, 10, main_sell=10) for i in range(9)]
    flip = bar(9, 9, 101.0, 10, main_buy=400)

    signals = feed(monitor, "3532", quiet + [flip])

    assert [s["label"] for s in signals] == ["主力累計翻多"]
    assert "量比 2.70×" in signals[0]["note"]


def test_bear_flip_mirrors_bull(monkeypatch):
    monitor = new_monitor(monkeypatch)
    bars = [bar(9, 0, 99.0, 100, main_buy=10)] + [bar(9, i, 100.0, 100, main_buy=10) for i in range(1, 9)]
    flip = bar(9, 9, 99.0, 100, main_sell=400)

    signals = feed(monitor, "3532", bars + [flip])

    assert [s["kind"] for s in signals] == [KIND_BEAR]
    assert signals[0]["label"] == "主力累計強勢翻空"
    assert "累計 -310 張" in signals[0]["note"]
    assert "主力淨額率 -63.27%" in signals[0]["note"]


def test_vwap_crossed_long_before_zero_axis_is_not_synchronized(monkeypatch):
    # VWAP早在09:02就站上、之後一直在上面，主力零軸卻到09:13才翻正：兩個穿越
    # 相隔超過SYNC_WINDOW，不算「同步」，不發訊號。
    monitor = new_monitor(monkeypatch)
    bars = [bar(9, 0, 100.0, 100, main_sell=10), bar(9, 1, 105.0, 100, main_sell=10)]
    bars += [bar(9, i, 105.0, 100, main_sell=10) for i in range(2, 11)]
    late_flip = bar(9, 11, 105.5, 100, main_buy=500)

    assert feed(monitor, "3532", bars + [late_flip]) == []
    assert feed(monitor, "3532", [bar(9, 12, 106.0, 100, main_buy=50)]) == []


def test_flip_during_first_bars_after_subscription_is_ignored(monkeypatch):
    # 較晚才被訂閱到的股票：累計值只從訂閱起算，前幾根「翻正」不可信；等到看滿
    # MIN_BARS根時，那次穿越也早就超出同步視窗，不會補發。
    monitor = new_monitor(monkeypatch)
    bars = [bar(9, 0, 100.0, 100, main_sell=10), bar(9, 1, 100.0, 100, main_sell=10), bar(9, 2, 101.0, 100, main_buy=400)]
    bars += [bar(9, i, 101.0, 100, main_buy=5) for i in range(3, 12)]

    assert feed(monitor, "3532", bars) == []


def test_missing_daily_history_or_thin_main_force_never_fires(monkeypatch):
    no_history = new_monitor(monkeypatch, avg_daily_volume=None)
    quiet = [bar(9, i, 100.0, 100, main_sell=10) for i in range(9)]
    assert feed(no_history, "3532", quiet + [bar(9, 9, 101.0, 100, main_buy=400)]) == []

    thin = new_monitor(monkeypatch)
    quiet = [bar(9, i, 100.0, 100, main_sell=1) for i in range(9)]
    assert feed(thin, "3532", quiet + [bar(9, 9, 101.0, 100, main_buy=15)]) == []


def test_tick_total_amount_and_volume_give_session_vwap_and_volume_ratio(monkeypatch):
    # tick自帶今日累計成交金額/成交量時，VWAP跟量比用它們算（不受訂閱起點影響）。
    monitor = new_monitor(monkeypatch)
    # 訂閱前已經成交5000張(均價100)：total_volume/total_amount從tick帶進來，
    # 訂閱起算的1分K只看得到每分鐘100張。
    quiet = [bar(9, i, 100.0, 100, main_sell=10, total_volume=(i + 1) * 100 + 5000,
                 total_amount=((i + 1) * 100 + 5000) * 100 * 1000)
             for i in range(9)]
    # 累計成交量6000張、金額換算VWAP≈100.02：收盤101站上VWAP；量比用6000張算。
    flip = bar(9, 9, 101.0, 100, main_buy=400, total_volume=6000,
               total_amount=5900 * 100 * 1000 + 100 * 101 * 1000)

    signals = feed(monitor, "3532", quiet + [flip])

    assert [s["kind"] for s in signals] == [KIND_BULL]
    assert "量比 162.00×" in signals[0]["note"]
