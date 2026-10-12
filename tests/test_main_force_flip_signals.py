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

    status = monitor.status()
    assert status["barsProcessed"] == 12
    assert status["firedBull"] == 1
    assert status["firedBear"] == 0
    assert status["lastBarAt"].startswith("2026-09-18T09:12")
    assert status["thresholds"]["minBars"] == module.MIN_BARS


def test_low_volume_ratio_is_still_labelled_strong_like_the_other_tool(monkeypatch):
    # 另一台工具的 14 筆全部標「強勢」，量比 1.85× 也是；預設不分強弱。
    monitor = new_monitor(monkeypatch)
    quiet = [bar(9, i, 100.0, 10, main_sell=10) for i in range(9)]
    flip = bar(9, 9, 101.0, 10, main_buy=400)

    signals = feed(monitor, "3532", quiet + [flip])

    assert [s["label"] for s in signals] == ["主力累計強勢翻多"]
    assert "量比 2.70×" in signals[0]["note"]


def test_strong_label_can_be_split_out_again_with_higher_thresholds(monkeypatch):
    monkeypatch.setattr(module, "NET_RATIO_STRONG", 0.40)
    monkeypatch.setattr(module, "VOLUME_RATIO_STRONG", 3.0)
    monitor = new_monitor(monkeypatch)
    quiet = [bar(9, i, 100.0, 10, main_sell=10) for i in range(9)]
    flip = bar(9, 9, 101.0, 10, main_buy=400)

    signals = feed(monitor, "3532", quiet + [flip])

    assert [s["label"] for s in signals] == ["主力累計翻多"]  # 量比 2.70× 未達 3×


def test_bear_flip_mirrors_bull(monkeypatch):
    # 從 10:00 才看到（較晚訂閱）：前面主力偏買、站上 VWAP，第 10 根大單倒出把累計翻負、跌破 VWAP。
    # 開頭那段從 0 翻正只是第一個方向、不算翻多（v5）。
    monitor = new_monitor(monkeypatch)
    bars = [bar(10, 0, 99.0, 100, main_buy=10)] + [bar(10, i, 100.0, 100, main_buy=10) for i in range(1, 9)]
    flip = bar(10, 9, 99.0, 100, main_sell=400)

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
    bars = [bar(10, 0, 100.0, 100, main_sell=10), bar(10, 1, 100.0, 100, main_sell=10), bar(10, 2, 101.0, 100, main_buy=400)]
    bars += [bar(10, i, 101.0, 100, main_buy=5) for i in range(3, 12)]

    assert feed(monitor, "3532", bars) == []


def test_first_direction_from_zero_at_the_open_is_not_a_flip(monkeypatch):
    # 正式環境 2026-09-22 的 3532（inspect 的實際數字）：09:01 主力累計 +134/156 張從 0 翻正，
    # 09:05 站上 VWAP、淨額率 23%、量比 5.3×。另一台工具沒有在 09:05 發（它 11:38 真的從負翻正才發）；
    # 2026-09-23 我們 09:05 一口氣發了 25 筆全是這種「開盤第一個方向」。v5：從 0 走出的方向不算翻。
    monitor = new_monitor(monkeypatch, avg_daily_volume=7378.8)
    monitor.enable_trace()
    bars = [
        bar(9, 0, 449.5, 214, main_buy=145, main_sell=11),
        bar(9, 1, 451.5, 142, main_buy=42, main_sell=8),
        bar(9, 2, 446.0, 149, main_buy=23, main_sell=63),
        bar(9, 3, 447.5, 98, main_buy=6, main_sell=36),
        bar(9, 4, 450.0, 118, main_buy=23, main_sell=31),
    ]
    assert feed(monitor, "3532", bars) == []
    assert monitor.trace()[0]["firstSign"] == "bull"
    assert not any(row.get("zeroCross") for row in monitor.trace())


def test_from_open_stock_skips_the_first_four_bars_then_fires_on_a_real_flip(monkeypatch):
    # 從開盤就看到、09:01～09:03 主力偏賣（累計 -55 張），09:04 大單敲進翻正、09:05 站上 VWAP：
    # 前 4 根一律不判定，09:05 那根才發，而且零軸時間是真的翻正的 09:04。
    monitor = new_monitor(monkeypatch)
    bars = [
        bar(9, 0, 100.0, 100, main_sell=40),
        bar(9, 1, 100.0, 100, main_sell=10),
        bar(9, 2, 100.0, 100, main_sell=5),
        bar(9, 3, 100.0, 100, main_buy=120),  # 收盤 09:04：累計 -55 → +65，真的從負翻正
    ]
    assert feed(monitor, "3532", bars) == []  # 09:01～09:04 一律不發

    signals = feed(monitor, "3532", [bar(9, 4, 101.0, 100, main_buy=60)])  # 收盤 09:05 站上 VWAP

    assert [s["label"] for s in signals] == ["主力累計強勢翻多"]
    assert signals[0]["barTs"] == ts(9, 5)
    assert "主力零軸 09:04" in signals[0]["note"]
    assert "VWAP穿越 09:05" in signals[0]["note"]
    assert "累計 +125 張" in signals[0]["note"]


def test_tiny_net_after_a_real_flip_is_blocked(monkeypatch):
    # 2026-09-23 的 5439 高技：淨額率 -6.67%、累計只有 -3 張，買賣互相抵銷的零頭不算翻空。
    monitor = new_monitor(monkeypatch)
    monitor.enable_trace()
    bars = [bar(10, 0, 99.0, 100, main_buy=10)] + [bar(10, i, 100.0, 100, main_buy=10) for i in range(1, 9)]  # 累計 +90、站上 VWAP
    dump = bar(10, 9, 99.0, 100, main_sell=100)  # 累計 -10、跌破 VWAP：真的翻負但太小
    assert feed(monitor, "5439", bars + [dump]) == []
    assert monitor.trace()[-1]["bear"]["blockers"] == ["主力累計 -10 張未達 -20 張"]


def test_flip_can_fire_again_after_the_cumulative_reverses(monkeypatch):
    # 另一台工具 3532 當天 11:38、11:57 各發一次翻多：累計翻負再翻正，就可以再發一次。
    monitor = new_monitor(monkeypatch)
    first = [bar(10, i, 100.0, 100, main_sell=10) for i in range(9)] + [bar(10, 9, 101.0, 100, main_buy=400)]
    assert [s["label"] for s in feed(monitor, "3532", first)] == ["主力累計強勢翻多"]

    # 主力倒貨把累計翻負、跌破 VWAP（翻空），再大買翻正、站上 VWAP：第二次翻多。
    reverse = [bar(10, 10, 99.0, 100, main_sell=700)]
    back = [bar(10, 11, 99.5, 100, main_buy=100), bar(10, 12, 101.5, 100, main_buy=400)]
    labels = [s["label"] for s in feed(monitor, "3532", reverse + back)]

    assert labels == ["主力累計強勢翻空", "主力累計強勢翻多"]


def test_one_sided_main_force_from_zero_is_not_a_flip(monkeypatch):
    # 正式環境 2026-09-22 的 1582 信錦：整個上午主力完全沒量，12:18 第一筆 101 張大單就是賣，
    # 累計從 0 直接變 -101、淨額率 -100%。另一台工具沒有這筆：只有一邊有過量，沒有「翻」可言。
    monitor = new_monitor(monkeypatch)
    monitor.enable_trace()
    quiet = [bar(9, i, 100.0, 100) for i in range(10)] + [bar(9, 10, 100.5, 100)]  # 先站上 VWAP
    dump = bar(9, 11, 99.0, 100, main_sell=101)  # 跌破 VWAP、累計從 0 翻負

    assert feed(monitor, "1582", quiet + [dump]) == []

    last = monitor.trace()[-1]
    # v5：從 0 走出的第一個方向連零軸穿越都不算，根本不會進到同步判定。
    assert last["firstSign"] == "bear" and last["vwapCross"] == "down"
    assert last.get("zeroCross") is None and "bear" not in last


def test_missing_daily_history_or_thin_main_force_never_fires(monkeypatch):
    no_history = new_monitor(monkeypatch, avg_daily_volume=None)
    quiet = [bar(9, i, 100.0, 100, main_sell=10) for i in range(9)]
    assert feed(no_history, "3532", quiet + [bar(9, 9, 101.0, 100, main_buy=400)]) == []

    thin = new_monitor(monkeypatch)
    quiet = [bar(9, i, 100.0, 100, main_sell=1) for i in range(9)]
    assert feed(thin, "3532", quiet + [bar(9, 9, 101.0, 100, main_buy=15)]) == []


def test_backfill_replays_day_from_kbars_and_persisted_main_force_bars(monkeypatch):
    # 使用者要求把「偵測器不在線那段」漏掉的訊號補回來：價量來自kbars（沒有主力欄位），
    # 主力張數/累計成交額來自當天主力副圖收集器已經落盤的main_force_bars。
    monitor = new_monitor(monkeypatch)
    monkeypatch.setattr(module, "get_main_force_flip_monitor", lambda: monitor)
    saved: list[dict] = []
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: saved.extend(rows) or rows)
    deleted: list[tuple] = []
    monkeypatch.setattr(module, "delete_signals_for_ticker", lambda d, t, kinds: deleted.append((d, t, set(kinds))) or 0)
    monkeypatch.setattr(module, "list_main_force_codes_for_date", lambda trade_date, interval="1m": ["3532", "9999"])

    kbars = [{"ts": ts(9, i), "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100} for i in range(9)]
    kbars.append({"ts": ts(9, 9), "open": 101.0, "high": 101.0, "low": 101.0, "close": 101.0, "volume": 100})
    monkeypatch.setattr(module, "get_stock_history_bars_1m",
                        lambda code, **kwargs: {"bars": kbars} if code == "3532" else {"bars": [], "error": "找不到股票合約"})
    main_rows = [{"ts": ts(9, i), "main_buy_volume": 0, "main_sell_volume": 10, "total_amount": 0} for i in range(9)]
    main_rows.append({"ts": ts(9, 9), "main_buy_volume": 400, "main_sell_volume": 0, "total_amount": 0})
    monkeypatch.setattr(module, "load_main_force_bars", lambda code, interval, trade_date=None: main_rows)

    result = module.backfill_flip_signals("2026-09-18")

    assert result["codeCount"] == 1  # 9999 不在族群清單裡，直接略過
    assert result["processed"] == 1
    assert result["barsReplayed"] == 10
    assert result["signalsEmitted"] == 1
    assert result["quotaBlocked"] is False
    assert deleted == [("2026-09-18", "3532", {KIND_BULL, KIND_BEAR})]
    assert [s["label"] for s in saved] == ["主力累計強勢翻多"]
    assert saved[0]["barTs"] == ts(9, 10)


def test_backfill_stops_and_reports_when_history_quota_is_exhausted(monkeypatch):
    monitor = new_monitor(monkeypatch)
    monkeypatch.setattr(module, "get_main_force_flip_monitor", lambda: monitor)
    monkeypatch.setattr(module, "delete_signals_for_ticker", lambda *a: 0)
    monkeypatch.setattr(module, "list_main_force_codes_for_date", lambda trade_date, interval="1m": ["3532", "2317"])
    calls: list[str] = []

    def exhausted(code, **kwargs):
        calls.append(code)
        return {"bars": [], "error": "history_quota_exhausted: Shioaji 歷史資料流量額度已用完"}

    monkeypatch.setattr(module, "get_stock_history_bars_1m", exhausted)

    result = module.backfill_flip_signals("2026-09-18")

    assert result["quotaBlocked"] is True
    assert result["processed"] == 0
    assert calls == ["2317"]  # 第一檔就撞到額度用完，整批停下、不再逐檔浪費


def test_backfill_skips_stocks_only_on_stock_futures_list(monkeypatch):
    # 2330 只在「股期標的」清單、不在 43 個族群裡：不重播、不花歷史額度（2026-09-24 使用者）
    monitor = new_monitor(monkeypatch)
    monkeypatch.setattr(module, "get_main_force_flip_monitor", lambda: monitor)
    monkeypatch.setattr(module, "list_main_force_codes_for_date", lambda trade_date, interval="1m": ["2330"])
    calls: list[str] = []
    monkeypatch.setattr(module, "get_stock_history_bars_1m", lambda code, **kwargs: calls.append(code) or {"bars": []})
    result = module.backfill_flip_signals("2026-09-18")
    assert calls == []
    assert result["processed"] == 0


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


def test_inspect_explains_which_filter_blocked_a_synchronized_flip(monkeypatch):
    # 跟另一台工具對條件用：零軸翻正、站上 VWAP 同步發生，但量比不夠時要說清楚是量比擋下；
    # 檢查用的重播不能寫入訊號、也不能動到即時偵測器。
    saved: list[dict] = []
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: saved.extend(rows) or rows)
    daily = [{"ts": f"2026-09-{10 + i:02d}", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100000}
             for i in range(5)]
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=6: daily)
    kbars = [{"ts": ts(9, i), "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100} for i in range(9)]
    kbars.append({"ts": ts(9, 9), "open": 101.0, "high": 101.0, "low": 101.0, "close": 101.0, "volume": 100})
    monkeypatch.setattr(module, "get_stock_history_bars_1m", lambda code, **kwargs: {"bars": kbars, "history_source": "finmind"})
    main_rows = [{"ts": ts(9, i), "main_buy_volume": 0, "main_sell_volume": 10, "total_amount": 0} for i in range(9)]
    main_rows.append({"ts": ts(9, 9), "main_buy_volume": 400, "main_sell_volume": 0, "total_amount": 0})
    monkeypatch.setattr(module, "load_main_force_bars", lambda code, interval, trade_date=None: main_rows)

    live_before = module.get_main_force_flip_monitor().status()["barsProcessed"]
    report = module.inspect_flip_signals("3532", "2026-09-18", include_trace=True)

    assert report["signals"] == []
    assert report["history"] == {"source": "finmind", "error": None, "dayBars": 10}
    assert report["mainForce"] == {"rows": 10, "matchedBars": 10}
    assert report["avgDailyVolume"] == 100000.0
    assert report["totals"]["cumNet"] == 310
    # 第一根主力偏賣：累計從 0 走出的第一個方向（不算翻），第 10 根才真的翻正。
    assert report["firstSign"] == {"time": "09:01", "dir": "bear"}
    assert report["zeroCrosses"] == [{"time": "09:10", "dir": "bull"}]
    assert report["vwapCrosses"] == [{"time": "09:10", "dir": "up"}]
    assert report["nearMissCount"] == 1
    blockers = report["nearMisses"][0]["bull"]["blockers"]
    assert len(blockers) == 1 and blockers[0].startswith("量比 0.27×"), blockers
    assert len(report["trace"]) == 10
    assert report["trace"][0]["skip"] == "warming_up"  # 開盤前 5 根不判定
    # 暖機中也要看得到三個比率，另一台工具若在前幾根就發訊號才對得出來。
    assert report["head"][0]["netRatio"] == -1.0
    assert report["head"][0]["volumeRatio"] == round(100 * 270 / 1 / 100000, 2)
    assert [row["time"] for row in report["crossStates"]] == ["09:01", "09:10"]
    assert report["crossStates"][0]["firstSign"] == "bear"
    assert report["crossStates"][1]["zeroCross"] == "bull" and report["crossStates"][1]["vwapCross"] == "up"
    assert report["trace"][-1]["netRatio"] == round(310 / 490, 4)
    assert saved == []
    assert module.get_main_force_flip_monitor().status()["barsProcessed"] == live_before  # 不動即時偵測器
