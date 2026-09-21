from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import stock_history_service
import intraday_kline_signals as module
from intraday_kline_signals import IntradayKlineSignalMonitor

TW_OFFSET_MS = 8 * 60 * 60 * 1000
BASE_DAY_UTC_MS = int(datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
FIVE_MIN_MS = 5 * 60 * 1000


def ts(hour: int, minute: int) -> int:
    """回傳2026-09-18當天，指定台北時間(hour:minute)的bar起始ms。"""
    return BASE_DAY_UTC_MS + (hour * 60 + minute) * 60 * 1000 - TW_OFFSET_MS


def bar(hour: int, minute: int, o: float, h: float, l: float, c: float) -> dict:
    return {"ts": ts(hour, minute), "open": o, "high": h, "low": l, "close": c}


def new_monitor(
    monkeypatch, prev_close: float | None = 100.0, prev_high: float | None = 101.0,
    ma_alignment_score: int | None = None,
):
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    previous = []
    if prev_close is not None:
        previous = [{"ts": "2026-09-17", "open": prev_close, "high": prev_high,
                     "low": prev_close, "close": prev_close, "volume": 1000}]
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: previous)
    # 創高黑龍用的六均線排列分數：跟load_daily_bars是不同的計算(需要240天
    # 歷史)，這裡直接mock掉分數本身，不用另外墊240筆假日K，跟其他測試
    # 意圖無關的訊號家族保持隔離。
    monkeypatch.setattr(module, "compute_ma_alignment_score", lambda code: ma_alignment_score)
    return IntradayKlineSignalMonitor()


def kinds(signals: list[dict]) -> list[str]:
    return [s["kind"] for s in signals]


def test_first_bar_only_establishes_baseline_no_signals(monkeypatch):
    monitor = new_monitor(monkeypatch)
    result = monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 101.5))
    assert result == []
    state = monitor._states["2330"]
    assert state.bar905_high == 102
    assert state.bar905_low == 99
    assert state.a8 == 100.5
    assert state.session_high == 102


def test_first_cross_905_high_fires_once_without_5ma_requirement(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=200.0)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 101.5))
    result = monitor.on_bar_completed("2330", bar(9, 5, 101.5, 103, 101, 102.5))
    assert "firstCross905High" in kinds(result)
    # 再次收盤更高，不會重複觸發（一天一次）。
    result2 = monitor.on_bar_completed("2330", bar(9, 10, 102.5, 104, 102, 103.5))
    assert "firstCross905High" not in kinds(result2)


def test_cross_up_905_needs_long_ok_and_5ma_and_retriggers(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=1000.0)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 101.5))  # 905高=102
    # 累積5根收盤才有5MA；先用平淡的K棒墊出5MA視窗。
    for minute in (5, 10, 15, 20):
        monitor.on_bar_completed("2330", bar(9, minute, 101, 101.5, 100.5, 101))
    # 這根收盤103，超過905高(102)且高於剛算出的5MA，應該觸發【5】。
    result = monitor.on_bar_completed("2330", bar(9, 25, 101, 104, 101, 103))
    assert "crossUp905" in kinds(result)
    cross = next(s for s in result if s["kind"] == "crossUp905")
    assert cross["note"] == "第1次"
    # 跌回905高以下
    monitor.on_bar_completed("2330", bar(9, 30, 103, 103, 100, 101))
    # 再次收盤突破905高且高於5MA，應該重新觸發，變成第2次。
    result2 = monitor.on_bar_completed("2330", bar(9, 35, 101, 105, 101, 104))
    assert "crossUp905" in kinds(result2)
    cross2 = next(s for s in result2 if s["kind"] == "crossUp905")
    assert cross2["note"] == "第2次"


def test_cross_up_905_blocked_when_long_precondition_fails(monkeypatch):
    # 905收盤102，相對昨收100，漲幅2%，但昨收改成96→漲幅6.25%(>=6%)，前置條件不成立。
    monitor = new_monitor(monkeypatch, prev_close=96.0, prev_high=1000.0)
    monitor.on_bar_completed("2330", bar(9, 0, 96, 102, 95, 102))
    for minute in (5, 10, 15, 20):
        monitor.on_bar_completed("2330", bar(9, minute, 101, 101.5, 100.5, 101))
    result = monitor.on_bar_completed("2330", bar(9, 25, 101, 104, 101, 103))
    assert "crossUp905" not in kinds(result)


def test_cross_up_prev_high_retriggers_on_leave_and_return(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=101.0)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 100.5, 99, 100))
    result = monitor.on_bar_completed("2330", bar(9, 5, 100, 102, 100, 101.5))
    assert "crossUpPrevHigh" in kinds(result)
    monitor.on_bar_completed("2330", bar(9, 10, 101.5, 101.5, 100, 100.5))  # 跌回昨日高以下
    result2 = monitor.on_bar_completed("2330", bar(9, 15, 100.5, 103, 100.5, 102))
    assert "crossUpPrevHigh" in kinds(result2)


def test_combo12_bull_fires_once_when_both_905_high_and_prev_high_broken(monkeypatch):
    # 條件1=905高(102)，條件2=昨日高(105)；長多前置條件故意設成不成立
    # (905K收盤反而低於昨收，long_ok第一個條件就不成立)，確認1+2多不受這個
    # 前置條件限制。prev_close=100讓漲停價=110，後面所有收盤價都留在合法
    # 漲跌停範圍內，不會被漲停鎖死邏輯誤擋。
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=105.0)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 99.5))  # 905高=102
    # 只過905高(103)，還沒過昨日高(105)：不該觸發。
    result1 = monitor.on_bar_completed("2330", bar(9, 5, 99.5, 104, 99.5, 103))
    assert "combo12Bull" not in kinds(result1)
    # 同時過905高(102)跟昨日高(105)：觸發。
    result2 = monitor.on_bar_completed("2330", bar(9, 10, 103, 107, 103, 106))
    assert "combo12Bull" in kinds(result2)
    # 再次同時滿足條件，不會重複觸發（一天一次）。
    result3 = monitor.on_bar_completed("2330", bar(9, 15, 106, 108, 106, 107))
    assert "combo12Bull" not in kinds(result3)


def test_combo12_bull_not_fired_when_only_prev_high_broken(monkeypatch):
    # 過昨日高(101)但還沒過905高(105)：不該觸發。
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=101.0)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 105, 99, 100.5))  # 905高=105
    result = monitor.on_bar_completed("2330", bar(9, 5, 100.5, 103, 100.5, 102))
    assert "combo12Bull" not in kinds(result)


def test_20ma_cross_needs_20_bars_and_fires_first_flag_once(monkeypatch):
    # prev_close=96：漲停價105.5，最後一根收盤105留在合法範圍內，同時
    # pct=(100/96-1)*100≈4.17%<6%且close>prev_close，維持long_ok=True
    # （crossUp20ma依規格需要long_ok成立才會觸發）。
    monitor = new_monitor(monkeypatch, prev_close=96.0, prev_high=200.0)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 101, 99, 100))
    minute = 5
    # 用19根平盤的K墊出滿20根視窗（含bar1共20根）；20MA在第20根才第一次算出來，
    # 這一根只用來建立「目前在20MA之下」的基準，還不能判定「跨越」（沒有前一根可比較）。
    for _ in range(19):
        result = monitor.on_bar_completed("2330", bar(9 + minute // 60, minute % 60, 100, 100.2, 99.8, 100))
        assert "crossUp20ma" not in kinds(result)
        minute += 5
    # 第21根收盤明顯拉高，才真正跨越20MA（跟第20根已經建立的「20MA之下」比較）。
    result = monitor.on_bar_completed("2330", bar(9 + minute // 60, minute % 60, 100, 106, 100, 105))
    assert "crossUp20ma" in kinds(result)
    assert "firstCrossUp20ma" in kinds(result)


def test_ma520_up_and_down_toggle(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=1000.0)
    minute = 0
    monitor.on_bar_completed("2330", bar(9, 0, 100, 100.5, 99.5, 100))
    minute = 5
    for _ in range(4):
        monitor.on_bar_completed("2330", bar(9, minute, 100, 100.2, 99.8, 100))
        minute += 5
    # 5MA/20MA都還沒有20筆；先確認ma5存在(5根)時520可以先算多方。
    result = monitor.on_bar_completed("2330", bar(9, minute, 100, 103, 100, 102))
    # ma20此時還沒有20筆資料，520不應該誤觸發。
    assert "ma520Up" not in kinds(result)


def test_a8_short_and_break905d_fire_once_before_1030(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=200.0)
    # 905高=102 905低=98 → A8=100
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 98, 100.5))
    # 收盤97.5同時跌破A8(100)跟905D(98)。
    result = monitor.on_bar_completed("2330", bar(9, 5, 100.5, 100.5, 97, 97.5))
    fired = kinds(result)
    assert "a8short" in fired
    assert "break905d" in fired
    # 一天一次：再跌一次不會重複。
    result2 = monitor.on_bar_completed("2330", bar(9, 10, 99, 99, 96, 96.5))
    assert "a8short" not in kinds(result2)
    assert "break905d" not in kinds(result2)


def test_a8_short_not_fired_after_1030(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=200.0)
    monitor.on_bar_completed("2330", bar(10, 30, 100, 102, 98, 100.5))
    result = monitor.on_bar_completed("2330", bar(10, 35, 100.5, 100.5, 90, 90))
    assert "a8short" not in kinds(result)
    assert "break905d" not in kinds(result)


def test_full_12short_family_flow_matches_spec_sequence(monkeypatch):
    # 注意：訊號判定用的是每根bar的「收盤時間」(bar["ts"]+5分)，不是bar起始時間；
    # 下面註解一律標示收盤時間，跟watch_stage/minute_of_day的判斷對齊。
    # 905高=100（bar1的high）→ session_high初始=100；_tick_size(100)=0.5（100不<100，落在<500檔位），
    # 5檔=2.5，區域=[97.5,100)。
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=1000.0)
    monitor.on_bar_completed("2330", bar(9, 0, 99, 100, 98, 99.5))  # 收09:05：建立基準
    # 收09:10（=WATCH_START_MINUTE）：收盤99.6在[97.5,100)內，開始偵測注意12空。
    r1 = monitor.on_bar_completed("2330", bar(9, 5, 99.5, 99.6, 99.4, 99.6))
    assert kinds(r1) == []
    state = monitor._states["2330"]
    assert state.watch_stage == "entering"
    # 收09:15：第1個等待K，仍未突破。
    r2 = monitor.on_bar_completed("2330", bar(9, 10, 99.6, 99.7, 99.5, 99.6))
    assert kinds(r2) == []
    assert state.watch_stage == "entering"
    # 收09:20：第2個等待K，仍未突破 → 注意12空成立。
    r3 = monitor.on_bar_completed("2330", bar(9, 15, 99.6, 99.7, 99.5, 99.6))
    assert "watch12short" in kinds(r3)
    assert state.watch_stage == "confirmed_watching_exit"
    # 收09:25：跌出5檔區域外（97.0<97.5）→ 離開，可以重新偵測。
    monitor.on_bar_completed("2330", bar(9, 20, 99.6, 99.6, 96.8, 97.0))
    assert state.watch_stage == "idle2_armed"
    # 收09:30：再次回到前高下方5檔內。
    monitor.on_bar_completed("2330", bar(9, 25, 97.0, 99.2, 97.0, 99.0))
    assert state.watch_stage == "entering2"
    # 收09:35：第1個等待K。
    monitor.on_bar_completed("2330", bar(9, 30, 99.0, 99.3, 98.8, 99.0))
    assert state.watch_stage == "entering2"
    # 收09:40：第2個等待K，仍未突破，且未超過10:30 → 12空成立。
    r4 = monitor.on_bar_completed("2330", bar(9, 35, 99.0, 99.3, 98.8, 99.0))
    assert "short12" in kinds(r4)
    assert state.watch_stage == "done"


def test_enhanced_12short_fires_independently_once_watch12_unlocked(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=1000.0)
    state = monitor._states.setdefault("2330", module._KlineState(trade_date="2026-09-18"))
    state.ever_watch12 = True
    state.above_20ma = True
    state.closes = [100.0] * 19  # 湊出20MA視窗（連同這根bar共20筆）
    # 收盤明顯走弱，讓20MA由上方跌到下方。
    signals = []

    def emit(kind, label, note="", ma20_down=None):
        signals.append({"kind": kind, "label": label, "note": note, "ma20Down": ma20_down})

    monitor._detect_20ma_cross(state, 90.0, 99.0, emit)
    assert "crossDown20ma" in kinds(signals)
    assert "enhanced12short" in kinds(signals)


def test_breakthrough_during_watch12_invalidates_and_updates_session_high(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=1000.0)
    monitor.on_bar_completed("2330", bar(9, 0, 99, 100, 98, 99.5))
    monitor.on_bar_completed("2330", bar(9, 5, 99.5, 99.5, 99, 99.3))
    monitor.on_bar_completed("2330", bar(9, 10, 99.3, 99.6, 99.4, 99.6))
    state = monitor._states["2330"]
    assert state.watch_stage == "entering"
    # 這一根創新高，突破前高 → 作廢，重新更新前高。
    monitor.on_bar_completed("2330", bar(9, 15, 99.6, 101.0, 99.5, 100.8))
    assert state.watch_stage == "idle"
    assert state.session_high == 101.0


def test_new_trade_date_resets_state_and_reloads_previous_day(monkeypatch):
    calls = []

    def fake_load(code, limit=1):
        calls.append(code)
        return [{"ts": "x", "open": 1, "high": 55.0, "low": 1, "close": 50.0, "volume": 1}]

    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", fake_load)
    monitor = IntradayKlineSignalMonitor()
    monitor.on_bar_completed("2330", bar(9, 0, 50, 51, 49, 50.5))
    # 重置新的一天現在會呼叫load_daily_bars兩次：一次拿昨收/昨高(limit=1)，
    # 一次拿創高黑龍用的前5日高點(limit=5)。
    assert calls == ["2330", "2330"]
    state = monitor._states["2330"]
    assert state.prev_close == 50.0
    assert state.prev_high == 55.0


def test_reset_for_backfill_rebuilds_state_instead_of_accumulating(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=105.0)
    monitor.reset_for_backfill("2330", "2026-09-18")
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 101.5))
    monitor.on_bar_completed("2330", bar(9, 5, 101.5, 103, 101, 102.5))
    assert monitor._states["2330"].bar_count == 2

    # 模擬重試回補同一天：狀態應該被重建成乾淨的一天，不會疊加成兩天份。
    monitor.reset_for_backfill("2330", "2026-09-18")
    state = monitor._states["2330"]
    assert state.bar_count == 0
    assert state.closes == []
    assert state.prev_close == 100.0
    assert state.prev_high == 105.0

    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 101.5))
    assert monitor._states["2330"].bar_count == 1


def test_reset_for_backfill_normalizes_code_case(monkeypatch):
    monitor = new_monitor(monkeypatch)
    monitor.reset_for_backfill("abc1", "2026-09-18")
    assert "ABC1" in monitor._states
    assert "abc1" not in monitor._states


def test_backfill_today_kline_signals_replays_bars_and_records_failures(monkeypatch):
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [
        {"ts": "2026-09-17", "open": 100.0, "high": 1000.0, "low": 100.0, "close": 100.0, "volume": 1},
    ])
    monkeypatch.setattr(module, "_monitor", None)

    def fake_history(code, *, calendar_days=3, service=None, hub=None):
        if code == "2330":
            bars = [
                bar(9, 0, 100, 102, 99, 101.5),
                bar(9, 5, 101.5, 103, 101, 102.5),  # 收盤102.5>905高(102)且>昨高(105不成立)…僅驗證重播筆數
                {**bar(9, 10, 1, 1, 1, 1), "ts": ts(9, 10) - 24 * 60 * 60 * 1000},  # 不同交易日，應被濾掉
            ]
            return {"status": "ok", "bars": bars}
        raise RuntimeError("history 服務暫時失敗")

    monkeypatch.setattr(module, "STOCK_GROUPS", {"測試群組": [("2330", "台積電"), ("2317", "鴻海")]})
    monkeypatch.setattr(stock_history_service, "get_stock_history_bars_5m", fake_history)

    result = module.backfill_today_kline_signals(trade_date="2026-09-18", delay=0)

    assert result["tradeDate"] == "2026-09-18"
    assert result["codeCount"] == 2
    assert result["codesProcessed"] == 1
    assert result["barsReplayed"] == 2  # 第三根不同交易日的bar被濾掉
    assert len(result["failures"]) == 1
    assert result["failures"][0]["code"] == "2317"

    monitor = module.get_intraday_kline_signal_monitor()
    assert monitor._states["2330"].bar_count == 2


def test_backfill_today_kline_signals_is_safe_to_rerun(monkeypatch):
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [])
    monkeypatch.setattr(module, "_monitor", None)
    monkeypatch.setattr(module, "STOCK_GROUPS", {"測試群組": [("2330", "台積電")]})
    monkeypatch.setattr(
        stock_history_service,
        "get_stock_history_bars_5m",
        lambda code, *, calendar_days=3, service=None, hub=None: {
            "status": "ok",
            "bars": [bar(9, 0, 100, 102, 99, 101.5), bar(9, 5, 101.5, 103, 101, 102.5)],
        },
    )

    first = module.backfill_today_kline_signals(trade_date="2026-09-18", delay=0)
    second = module.backfill_today_kline_signals(trade_date="2026-09-18", delay=0)

    assert first["barsReplayed"] == second["barsReplayed"] == 2
    # 重播兩次，state不會疊加成4根bar；reset_for_backfill讓重跑保持乾淨。
    monitor = module.get_intraday_kline_signal_monitor()
    assert monitor._states["2330"].bar_count == 2


def test_backfill_deletes_existing_kline_signals_before_replaying_when_bars_are_found(monkeypatch):
    # 即時路徑可能因為股票訂閱較晚才啟動，把「當天第一根真正被偵測到的
    # bar」誤判成905基準bar，算出偏晚的錯誤訊號時間並先存進DB。
    # ONCE_PER_DAY_KINDS去重會讓backfill算出的正確時間被這筆舊資料擋掉，
    # 所以回補重播前要先清掉這檔股票當天的K線訊號家族舊紀錄，讓歷史
    # kbars重算出來的版本(不受即時訂閱時機影響)才是準的。
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [])
    monkeypatch.setattr(module, "_monitor", None)
    monkeypatch.setattr(module, "STOCK_GROUPS", {"測試群組": [("2330", "台積電")]})
    monkeypatch.setattr(
        stock_history_service,
        "get_stock_history_bars_5m",
        lambda code, *, calendar_days=3, service=None, hub=None: {
            "status": "ok",
            "bars": [bar(9, 0, 100, 102, 99, 101.5), bar(9, 5, 101.5, 103, 101, 102.5)],
        },
    )
    deleted_for = []
    monkeypatch.setattr(module, "delete_kline_signals_for_ticker", lambda trade_date, ticker: deleted_for.append((trade_date, ticker)))

    module.backfill_today_kline_signals(trade_date="2026-09-18", delay=0)

    assert deleted_for == [("2026-09-18", "2330")]


def test_backfill_does_not_delete_when_no_bars_found_for_the_date(monkeypatch):
    # 抓資料失敗/當天沒有bars時不該先刪除既有資料——不然會把正確的舊
    # 資料清空、卻補不回新資料，比什麼都不做更糟。
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [])
    monkeypatch.setattr(module, "_monitor", None)
    monkeypatch.setattr(module, "STOCK_GROUPS", {"測試群組": [("2330", "台積電")]})
    monkeypatch.setattr(
        stock_history_service,
        "get_stock_history_bars_5m",
        lambda code, *, calendar_days=3, service=None, hub=None: {"status": "ok", "bars": []},
    )
    deleted_for = []
    monkeypatch.setattr(module, "delete_kline_signals_for_ticker", lambda trade_date, ticker: deleted_for.append((trade_date, ticker)))

    module.backfill_today_kline_signals(trade_date="2026-09-18", delay=0)

    assert deleted_for == []


def test_start_kline_signal_backfill_today_threads_trade_date_through(monkeypatch):
    received = {}

    def fake_backfill(*, trade_date=None, **kwargs):
        received["trade_date"] = trade_date
        return {"tradeDate": trade_date, "codeCount": 0, "codesProcessed": 0,
                "barsReplayed": 0, "signalsEmitted": 0, "failures": []}

    monkeypatch.setattr(module, "backfill_today_kline_signals", fake_backfill)
    monkeypatch.setattr(module, "_backfill_status", {"running": False, "result": None})

    module.start_kline_signal_backfill_today(trade_date="2026-09-18")
    for _ in range(50):
        if not module.kline_signal_backfill_status()["running"]:
            break
        time.sleep(0.05)
    assert received["trade_date"] == "2026-09-18"
    assert module.kline_signal_backfill_status()["result"]["tradeDate"] == "2026-09-18"


def test_start_kline_signal_backfill_today_blocks_duplicate_and_reports_result(monkeypatch):
    started_event = threading.Event()
    release_event = threading.Event()

    def fake_backfill(**kwargs):
        started_event.set()
        assert release_event.wait(timeout=5), "release_event 逾時未被觸發"
        return {
            "tradeDate": "2026-09-18", "codeCount": 1, "codesProcessed": 1,
            "barsReplayed": 3, "signalsEmitted": 1, "failures": [],
        }

    monkeypatch.setattr(module, "backfill_today_kline_signals", fake_backfill)
    monkeypatch.setattr(module, "_backfill_status", {"running": False, "result": None})

    result = module.start_kline_signal_backfill_today()
    assert result == {"started": True}
    assert started_event.wait(timeout=5), "背景執行緒逾時未啟動"
    assert module.kline_signal_backfill_status()["running"] is True

    duplicate = module.start_kline_signal_backfill_today()
    assert duplicate == {"started": False, "reason": "already_running"}

    release_event.set()
    for _ in range(50):
        if not module.kline_signal_backfill_status()["running"]:
            break
        time.sleep(0.1)
    status = module.kline_signal_backfill_status()
    assert status["running"] is False
    assert status["result"]["barsReplayed"] == 3


def test_start_kline_signal_backfill_today_records_error_result(monkeypatch):
    def fake_backfill(**kwargs):
        raise RuntimeError("回補整體失敗")

    monkeypatch.setattr(module, "backfill_today_kline_signals", fake_backfill)
    monkeypatch.setattr(module, "_backfill_status", {"running": False, "result": None})

    module.start_kline_signal_backfill_today()
    for _ in range(50):
        if not module.kline_signal_backfill_status()["running"]:
            break
        time.sleep(0.1)
    status = module.kline_signal_backfill_status()
    assert status["running"] is False
    assert "回補整體失敗" in status["result"]["error"]


def test_limit_up_hit_suppresses_all_further_signal_detection(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=1000.0)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 101.5))  # 905K基準
    state = monitor._states["2330"]
    assert state.limit_up == 110.0
    assert state.limit_down == 90.0
    assert state.limit_hit is False

    # 收盤剛好等於漲停價：鎖死，這根本身也不該有任何訊號。
    result = monitor.on_bar_completed("2330", bar(9, 5, 108, 110, 108, 110.0))
    assert result == []
    assert state.limit_hit is True
    bar_count_after_hit = state.bar_count

    # 之後即使走勢看起來會觸發訊號（例如帶量急跌），鎖死後也不該再偵測。
    result2 = monitor.on_bar_completed("2330", bar(9, 10, 110.0, 110.0, 95.0, 96.0))
    assert result2 == []
    assert state.bar_count == bar_count_after_hit  # 鎖死後不再累積bar_count/closes


def test_limit_down_hit_suppresses_all_further_signal_detection(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=1000.0)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 101, 96, 96.5))  # 905K基準
    state = monitor._states["2330"]
    assert state.limit_down == 90.0

    result = monitor.on_bar_completed("2330", bar(9, 5, 92, 92, 90, 90.0))
    assert result == []
    assert state.limit_hit is True

    result2 = monitor.on_bar_completed("2330", bar(9, 10, 90.0, 105.0, 90.0, 104.0))
    assert result2 == []


def test_limit_hit_does_not_carry_over_to_next_trade_date(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=1000.0)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 101.5))
    monitor.on_bar_completed("2330", bar(9, 5, 108, 110, 108, 110.0))
    assert monitor._states["2330"].limit_hit is True

    monitor.reset_for_backfill("2330", "2026-09-19")
    state = monitor._states["2330"]
    assert state.limit_hit is False
    assert state.limit_up == 110.0  # 重新用(固定mock的)前一天收盤價換算，不是延續昨天鎖死的殘留值


def _new_ots_state():
    """12空(五分K)新版機制的隔離測試專用：直接建立20筆平盤歷史墊出
    MA20視窗，不透過完整on_bar_completed走一遍（那樣墊20根視窗太冗長）。"""
    state = module._KlineState(trade_date="2026-09-18")
    state.bar905_high = 102.0
    state.bar905_low = 99.0
    state.closes = [100.0] * 19
    state.lows = [99.5] * 19
    return state


def _ots_step(monitor, state, signals, close_v, high_v, low_v, slope=None):
    state.closes.append(close_v)
    state.lows.append(low_v)
    if slope is not None:
        state.ma20_slope = slope
    ma20 = module._moving_average(state.closes, 20)

    def emit(kind, label, note="", ma20_down=None):
        signals.append(kind)

    monitor._detect_one_two_short(state, close_v, high_v, low_v, ma20, emit)


def test_one_two_short_fires_after_full_five_step_sequence(monkeypatch):
    monitor = new_monitor(monkeypatch)
    state = _new_ots_state()
    signals: list[str] = []

    _ots_step(monitor, state, signals, 100.0, 100.5, 98.5)  # ①破905低
    assert state.ots_stage == "tracking_1high"
    _ots_step(monitor, state, signals, 100.0, 101.0, 99.5)  # ②墊1高=101(<905高102)
    assert state.ots_stage == "tracking_1high" and state.ots_1high == 101.0
    _ots_step(monitor, state, signals, 95.0, 99.0, 94.0, slope="down")  # ③破位
    assert state.ots_stage == "tracking_2high"
    _ots_step(monitor, state, signals, 96.0, 96.0, 95.0)  # ④墊2高，收盤未比前一根低，不觸發
    assert signals == []
    _ots_step(monitor, state, signals, 97.0, 98.0, 96.5)  # ④墊2高續(=98，仍<1高101)
    assert state.ots_2high == 98.0 and signals == []
    _ots_step(monitor, state, signals, 93.0, 94.0, 90.0, slope="down")  # ⑤再轉弱，正式觸發
    assert signals == ["oneTwoShort"]
    assert state.ots_stage == "done"

    # 一天一次：之後再怎麼走都不該再觸發。
    _ots_step(monitor, state, signals, 80.0, 85.0, 79.0, slope="down")
    assert signals == ["oneTwoShort"]


def test_one_two_short_invalidates_when_1high_reaches_905_high(monkeypatch):
    monitor = new_monitor(monkeypatch)
    state = _new_ots_state()
    signals: list[str] = []

    _ots_step(monitor, state, signals, 100.0, 100.5, 98.5)  # 破905低
    _ots_step(monitor, state, signals, 100.0, 102.0, 99.5)  # 1高衝到=905高 -> 作廢
    assert state.ots_stage == "idle"
    assert state.ots_1high is None
    # 作廢後可以重新開始：再跌破905低應該重新進入tracking_1high。
    _ots_step(monitor, state, signals, 100.0, 98.5, 98.0)
    assert state.ots_stage == "tracking_1high"


def test_one_two_short_invalidates_when_2high_reaches_1high(monkeypatch):
    monitor = new_monitor(monkeypatch)
    state = _new_ots_state()
    signals: list[str] = []

    _ots_step(monitor, state, signals, 100.0, 100.5, 98.5)  # 破905低
    _ots_step(monitor, state, signals, 100.0, 101.0, 99.5)  # 墊1高=101
    _ots_step(monitor, state, signals, 95.0, 99.0, 94.0, slope="down")  # 破位
    assert state.ots_stage == "tracking_2high"
    _ots_step(monitor, state, signals, 101.0, 101.0, 95.0)  # 2高衝到=1高 -> 作廢
    assert state.ots_stage == "idle"
    assert state.ots_1high is None and state.ots_2high is None
    assert signals == []


def _black_dragon_monitor(monkeypatch, ma_alignment_score=10, five_day_high=108.0, in_groups=True):
    monkeypatch.setattr(module, "_group_lookup_cache", None)
    monkeypatch.setattr(
        module, "STOCK_GROUPS",
        {"測試群組": [("2330", "台積電")]} if in_groups else {},
    )
    # prev_close=105讓漲跌停範圍是[94.5,115.5]，測試用的09:00開盤110、
    # 11:00那根的高低收都留在合法範圍內，不會被漲停/跌停鎖死邏輯誤擋。
    monitor = new_monitor(
        monkeypatch, prev_close=105.0, prev_high=1000.0, ma_alignment_score=ma_alignment_score,
    )

    def fake_load(code, limit=1):
        if limit >= 5:
            return [{"high": five_day_high, "close": 100.0}] * 5
        return [{"close": 105.0, "high": 1000.0}]  # 跟new_monitor的prev_close=105一致

    monkeypatch.setattr(module, "load_daily_bars", fake_load)
    return monitor


def test_black_dragon_fires_when_all_conditions_met(monkeypatch):
    monitor = _black_dragon_monitor(monkeypatch, ma_alignment_score=10, five_day_high=108.0)
    monitor.on_bar_completed("2330", bar(9, 0, 110, 111, 109, 110.5))  # today_open=110
    # 11:00那根：高點109>前5日高點108(創高)，收盤105<今日開盤110。
    result = monitor.on_bar_completed("2330", bar(11, 0, 107, 109.0, 106, 105.0))
    assert "blackDragon" in kinds(result)


def test_black_dragon_does_not_fire_before_1100(monkeypatch):
    monitor = _black_dragon_monitor(monkeypatch)
    monitor.on_bar_completed("2330", bar(9, 0, 110, 111, 109, 110.5))
    result = monitor.on_bar_completed("2330", bar(10, 55, 107, 109.0, 106, 105.0))
    assert "blackDragon" not in kinds(result)


def test_black_dragon_equal_high_does_not_count_as_breakout(monkeypatch):
    monitor = _black_dragon_monitor(monkeypatch, five_day_high=108.0)
    monitor.on_bar_completed("2330", bar(9, 0, 110, 111, 109, 110.5))
    # 高點剛好=前5日高點(平高)，不算創高。
    result = monitor.on_bar_completed("2330", bar(11, 0, 107, 108.0, 106, 105.0))
    assert "blackDragon" not in kinds(result)


def test_black_dragon_requires_close_below_today_open(monkeypatch):
    monitor = _black_dragon_monitor(monkeypatch, five_day_high=108.0)
    monitor.on_bar_completed("2330", bar(9, 0, 110, 111, 109, 110.5))
    # 收盤110.5沒有低於今日開盤110。
    result = monitor.on_bar_completed("2330", bar(11, 0, 107, 109.0, 106, 110.5))
    assert "blackDragon" not in kinds(result)


def test_black_dragon_requires_ma_alignment_score_at_least_10(monkeypatch):
    monitor = _black_dragon_monitor(monkeypatch, ma_alignment_score=9, five_day_high=108.0)
    monitor.on_bar_completed("2330", bar(9, 0, 110, 111, 109, 110.5))
    result = monitor.on_bar_completed("2330", bar(11, 0, 107, 109.0, 106, 105.0))
    assert "blackDragon" not in kinds(result)


def test_black_dragon_requires_stock_in_official_groups(monkeypatch):
    monitor = _black_dragon_monitor(monkeypatch, five_day_high=108.0, in_groups=False)
    monitor.on_bar_completed("2330", bar(9, 0, 110, 111, 109, 110.5))
    result = monitor.on_bar_completed("2330", bar(11, 0, 107, 109.0, 106, 105.0))
    assert "blackDragon" not in kinds(result)


def test_black_dragon_fires_only_once_per_day(monkeypatch):
    monitor = _black_dragon_monitor(monkeypatch, five_day_high=108.0)
    monitor.on_bar_completed("2330", bar(9, 0, 110, 111, 109, 110.5))
    result1 = monitor.on_bar_completed("2330", bar(11, 0, 107, 109.0, 106, 105.0))
    assert "blackDragon" in kinds(result1)
    result2 = monitor.on_bar_completed("2330", bar(11, 5, 105, 110.0, 104, 103.0))
    assert "blackDragon" not in kinds(result2)
