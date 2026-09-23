from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

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


def prev_day_bar(hour: int, minute: int, close: float, low: float | None = None, days_ago: int = 1) -> dict:
    """幾天前（預設昨天2026-09-17）的5分K，給MA20跨日種子用。"""
    low = close - 0.5 if low is None else low
    return {"ts": ts(hour, minute) - days_ago * 24 * 60 * 60 * 1000,
            "open": close, "high": close + 0.5, "low": low, "close": close, "volume": 10}


@pytest.fixture(autouse=True)
def _isolated_bars_5m_store(monkeypatch):
    """預設沒有昨天的5分K種子、也不真的寫本機資料庫；要測種子的測試自己再覆蓋。"""
    monkeypatch.setattr(module, "load_stock_bars_5m_before", lambda code, trade_date, limit: [])
    monkeypatch.setattr(module, "save_stock_bars_5m_many", lambda bars_by_code: 0)
    monkeypatch.setattr(module, "save_stock_bars_5m", lambda code, bars: 0)
    monkeypatch.setattr(module, "prune_stock_bars_5m", lambda *args, **kwargs: 0)
    monkeypatch.setattr(module, "bars_5m_coverage_complete", lambda code, trade_date: False)
    monkeypatch.setattr(module, "_last_reset_trade_date", None)
    monkeypatch.setattr(module, "_pending_bars_5m", {})
    monkeypatch.setattr(module, "_pending_bars_count", 0)


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
    monkeypatch.setattr(module, "latest_daily_trade_date_before", lambda trade_date: None)
    module._market_prev_cache.clear()
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


def test_late_first_bar_skips_baseline_and_suppresses_905_family_signals(monkeypatch):
    # 即時路徑的股票訂閱是動態、有上限的：某檔股票如果比較晚才被訂閱到，
    # monitor收到的「第一根」bar其實是當天較晚才完整走完的某根K棒，不是
    # 真正的09:00-09:05。這裡模擬09:30才收到第一根bar(收盤09:35，遠超過
    # FIRST_BAR_MAX_CLOSE_MINUTE=09:10緩衝)，確認不會把它誤當905基準。
    monitor = new_monitor(monkeypatch, prev_close=100.0, prev_high=101.0)
    result = monitor.on_bar_completed("2330", bar(9, 30, 100, 102, 99, 101.5))
    assert result == []
    state = monitor._states["2330"]
    assert state.bar905_high is None
    assert state.bar905_low is None
    assert state.a8 is None
    assert state.today_open is None
    assert state.late_subscription is True

    # 之後同時「過905高」跟「過昨日高」也不該補發1+2多／905系列訊號——
    # 沒有可信的基準，寧可當天沒訊號、也不要給錯的訊號。「過昨日高」本身
    # 不依賴905基準(昨日高來自load_daily_bars，跟今天即時訂閱時機無關)，
    # 繼續正常觸發沒有問題，只有真正依賴905基準的家族要被壓下來。
    result2 = monitor.on_bar_completed("2330", bar(9, 35, 101.5, 110, 101.5, 109))
    assert "combo12Bull" not in kinds(result2)
    assert "firstCross905High" not in kinds(result2)
    assert "crossUp905" not in kinds(result2)
    assert "crossUpPrevHigh" in kinds(result2)


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


def test_new_trade_date_resets_state_and_reloads_previous_day(monkeypatch):
    calls = []

    def fake_load(code, limit=1):
        calls.append(code)
        return [{"ts": "2026-09-17", "open": 1, "high": 55.0, "low": 1, "close": 50.0, "volume": 1}]

    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", fake_load)
    monkeypatch.setattr(module, "latest_daily_trade_date_before", lambda trade_date: "2026-09-17")
    monitor = IntradayKlineSignalMonitor()
    monitor.on_bar_completed("2330", bar(9, 0, 50, 51, 49, 50.5))
    # 重置新的一天只呼叫一次load_daily_bars（最近幾根），昨收/昨高跟創高黑龍用的前5日高點都從裡面挑。
    assert calls == ["2330"]
    state = monitor._states["2330"]
    assert state.prev_close == 50.0
    assert state.prev_high == 55.0


def test_stale_previous_day_bar_is_ignored_so_prev_high_signals_cannot_fire(monkeypatch):
    # 2026-09-23 的 6218（上櫃）：櫃買來源被擋、日K停在更早的日子，拿舊高點當昨高，09:10 沒過昨高卻發了 1+2多。
    # 這檔最新的日K比全市場上一個交易日舊 → 當作沒有昨日資料：不判 1+2多／過昨高，也不算漲跌停價。
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "compute_ma_alignment_score", lambda code: None)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [
        {"ts": "2026-09-15", "open": 60.0, "high": 61.0, "low": 59.0, "close": 60.0, "volume": 1000}])
    monkeypatch.setattr(module, "latest_daily_trade_date_before", lambda trade_date: "2026-09-17")
    module._market_prev_cache.clear()
    monitor = IntradayKlineSignalMonitor()
    monitor.on_bar_completed("6218", bar(9, 0, 60.0, 61.5, 59.5, 61.0))
    state = monitor._states["6218"]
    assert state.prev_high is None and state.prev_close is None and state.limit_up is None
    # 過 905 高又過（舊的）昨高，也不能發 1+2多／過昨日高
    signals = monitor.on_bar_completed("6218", bar(9, 5, 61.0, 63.5, 61.0, 63.4))
    assert "combo12Bull" not in kinds(signals) and "crossUpPrevHigh" not in kinds(signals)
    assert "firstCross905High" in kinds(signals)  # 只靠今天自己的 905 高的訊號照常

    # 同一檔如果日K有跟上（就是市場上一個交易日的那根），照常判定
    module._market_prev_cache.clear()
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [
        {"ts": "2026-09-17", "open": 60.0, "high": 61.0, "low": 59.0, "close": 60.0, "volume": 1000}])
    fresh = IntradayKlineSignalMonitor()
    fresh.on_bar_completed("6218", bar(9, 0, 60.0, 61.5, 59.5, 61.0))
    assert fresh._states["6218"].prev_high == 61.0
    assert "combo12Bull" in kinds(fresh.on_bar_completed("6218", bar(9, 5, 61.0, 63.5, 61.0, 63.4)))


def test_previous_day_bar_older_than_twelve_days_is_ignored_even_without_market_reference(monkeypatch):
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "compute_ma_alignment_score", lambda code: None)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [
        {"ts": "2026-08-20", "open": 60.0, "high": 61.0, "low": 59.0, "close": 60.0, "volume": 1000}])
    monkeypatch.setattr(module, "latest_daily_trade_date_before", lambda trade_date: None)
    module._market_prev_cache.clear()
    monitor = IntradayKlineSignalMonitor()
    monitor.on_bar_completed("6218", bar(9, 0, 60.0, 61.5, 59.5, 61.0))
    assert monitor._states["6218"].prev_high is None


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

    def fake_history(code, *, calendar_days=3, service=None, hub=None, **kwargs):
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
        lambda code, *, calendar_days=3, service=None, hub=None, **kwargs: {
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
        lambda code, *, calendar_days=3, service=None, hub=None, **kwargs: {
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
        lambda code, *, calendar_days=3, service=None, hub=None, **kwargs: {"status": "ok", "bars": []},
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
        # 最近 5 根日K：昨收 105（跟 new_monitor 的 prev_close=105 一致）、前 5 日高點 = five_day_high
        return [{"high": five_day_high, "close": 105.0}] * 5

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


# ---------------------------------------------------------------------------
# 5分K MA20 跨日接續（昨天最後21根當種子）
# ---------------------------------------------------------------------------

def _yesterday_seed_bars(closes: list[float], lows: list[float] | None = None) -> list[dict]:
    """昨天收盤前最後len(closes)根5分K（13:30往回推），舊到新。"""
    bars = []
    count = len(closes)
    for index, close in enumerate(closes):
        minute_from_open = (13 * 60 + 25) - (count - 1 - index) * 5
        low = lows[index] if lows else None
        bars.append(prev_day_bar(minute_from_open // 60, minute_from_open % 60, close, low))
    return bars


def test_seeded_previous_day_bars_give_ma20_and_slope_from_first_bar(monkeypatch):
    monitor = new_monitor(monkeypatch)
    # 昨天最後21根一路走低：MA20 下彎、昨收在 20MA 下方。
    seeds = _yesterday_seed_bars([110.0 - i * 0.5 for i in range(21)])
    monkeypatch.setattr(module, "load_stock_bars_5m_before", lambda code, trade_date, limit: seeds)

    result = monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 100.5))

    assert result == []  # 第一根照舊只建立基準、不發訊號
    state = monitor._states["2330"]
    assert state.seed_count == 21
    assert state.bar_count == 1
    assert len(state.closes) == 22  # 21根種子＋今天第一根
    assert state.ma20_slope == "down"
    assert state.prev_ma20 == pytest.approx(module._moving_average(state.closes, 20))
    assert state.above_20ma is False  # 最後20根（109…100 加今天的100.5）MA20 約 104.3，100.5 在下方


def test_seeds_do_not_emit_ma_signals_by_themselves_and_cross_is_relative_to_yesterday(monkeypatch):
    monitor = new_monitor(monkeypatch, prev_close=98.0, prev_high=101.0)  # 第一根收99>昨收98：long_ok
    seeds = _yesterday_seed_bars([100.0] * 21)  # 平的 MA20 = 100
    monkeypatch.setattr(module, "load_stock_bars_5m_before", lambda code, trade_date, limit: seeds)

    first = monitor.on_bar_completed("2330", bar(9, 0, 99.0, 99.5, 98.5, 99.0))  # 收在 20MA 下
    assert first == []
    state = monitor._states["2330"]
    assert state.above_20ma is False
    assert state.in_520_short is True  # 昨天平盤、今天開低：一開始就在五二零空的狀態，不算新事件

    second = monitor.on_bar_completed("2330", bar(9, 5, 99.0, 100.6, 99.0, 100.5))  # 站回 20MA 上
    assert "crossUp20ma" in kinds(second)  # 沒種子時要到第21根才可能出現
    assert "firstCrossUp20ma" in kinds(second)
    assert "ma520Up" in kinds(second)


def test_seed_bars_are_ignored_when_older_than_market_previous_trade_date(monkeypatch):
    monitor = new_monitor(monkeypatch)
    monkeypatch.setattr(module, "latest_daily_trade_date_before", lambda trade_date: "2026-09-17")
    module._market_prev_cache.clear()
    stale = _yesterday_seed_bars([100.0] * 21)
    stale = [{**b, "ts": b["ts"] - 2 * 24 * 60 * 60 * 1000} for b in stale]  # 09-15 的 K，缺 09-17
    monkeypatch.setattr(module, "load_stock_bars_5m_before", lambda code, trade_date, limit: stale)

    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 100.5))
    state = monitor._states["2330"]
    assert state.seed_count == 0
    assert state.closes == [100.5]
    assert state.prev_ma20 is None


def test_seed_bars_older_than_twelve_days_are_ignored_without_market_reference(monkeypatch):
    monitor = new_monitor(monkeypatch)
    old = [{**b, "ts": b["ts"] - 20 * 24 * 60 * 60 * 1000} for b in _yesterday_seed_bars([100.0] * 21)]
    monkeypatch.setattr(module, "load_stock_bars_5m_before", lambda code, trade_date, limit: old)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 100.5))
    assert monitor._states["2330"].seed_count == 0


def test_seed_loader_failure_falls_back_to_today_only(monkeypatch):
    monitor = new_monitor(monkeypatch)

    def boom(code, trade_date, limit):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(module, "load_stock_bars_5m_before", boom)
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 100.5))
    assert monitor._states["2330"].seed_count == 0
    assert monitor._states["2330"].bar_count == 1


def test_seeds_keep_only_last_21_bars_before_trade_date(monkeypatch):
    seeds = _yesterday_seed_bars([100.0 + i for i in range(30)])
    todays = [bar(9, 0, 1, 1, 1, 1)]  # 今天的 K 不能混進種子
    result = module.previous_day_bars_5m("2330", "2026-09-18", seeds + todays)
    assert len(result) == module.SEED_BARS == 21
    assert [b["close"] for b in result] == [109.0 + i for i in range(21)]


def test_live_bars_are_queued_and_flushed_in_one_batch(monkeypatch):
    monitor = new_monitor(monkeypatch)
    written: list[dict] = []
    monkeypatch.setattr(module, "save_stock_bars_5m_many", lambda batch: written.append(batch) or 1)
    monkeypatch.setattr(module, "PERSIST_FLUSH_SECONDS", 10_000.0)

    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 100.5))
    monitor.on_bar_completed("2317", bar(9, 0, 50, 51, 49, 50.5))
    assert written == []  # 還沒到時間、也沒累積到門檻，先排隊
    assert module._pending_bars_count == 2

    assert module.flush_pending_bars_5m(force=True) == 1
    assert list(written[0]) == ["2330", "2317"]
    assert written[0]["2330"][0]["close"] == 100.5
    assert module._pending_bars_count == 0


def test_first_bar_of_a_new_day_force_flushes_yesterdays_pending_bars(monkeypatch):
    monitor = new_monitor(monkeypatch)
    written: list[dict] = []
    monkeypatch.setattr(module, "save_stock_bars_5m_many", lambda batch: written.append(batch) or 1)
    monkeypatch.setattr(module, "PERSIST_FLUSH_SECONDS", 10_000.0)

    yesterday = {**bar(13, 25, 100, 101, 99, 100.5), "ts": ts(13, 25) - 24 * 60 * 60 * 1000}
    monitor.on_bar_completed("2330", yesterday)
    assert written == []
    monitor.on_bar_completed("2330", bar(9, 0, 100, 102, 99, 100.5))  # 新的一天第一根
    assert len(written) == 1 and written[0]["2330"][0]["ts"] == yesterday["ts"]


def test_backfill_replays_with_persist_off_seeds_from_kbars_and_stores_all_bars(monkeypatch):
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [
        {"ts": "2026-09-17", "open": 100.0, "high": 1000.0, "low": 100.0, "close": 100.0, "volume": 1},
    ])
    monkeypatch.setattr(module, "latest_daily_trade_date_before", lambda trade_date: "2026-09-17")
    module._market_prev_cache.clear()
    monkeypatch.setattr(module, "_monitor", None)
    queued: list[str] = []
    monkeypatch.setattr(module, "queue_bar_5m", lambda code, b: queued.append(code))
    stored: dict[str, int] = {}
    monkeypatch.setattr(module, "save_stock_bars_5m", lambda code, bars: stored.setdefault(code, len(bars)))
    pruned = []
    monkeypatch.setattr(module, "prune_stock_bars_5m", lambda *a, **k: pruned.append(True) or 7)

    seeds = _yesterday_seed_bars([100.0] * 21)

    def fake_history(code, *, calendar_days=3, service=None, hub=None, **kwargs):
        return {"status": "ok", "bars": seeds + [bar(9, 0, 100, 102, 99, 101.5), bar(9, 5, 101.5, 103, 101, 102.5)]}

    monkeypatch.setattr(module, "STOCK_GROUPS", {"測試群組": [("2330", "台積電")]})
    monkeypatch.setattr(stock_history_service, "get_stock_history_bars_5m", fake_history)

    result = module.backfill_today_kline_signals(trade_date="2026-09-18", delay=0)

    assert result["barsReplayed"] == 2
    assert result["barsStored"] == 23  # 昨天21根＋今天2根一起存
    assert result["barsPruned"] == 7 and pruned == [True]
    assert queued == []  # 回補不走即時佇列
    state = module.get_intraday_kline_signal_monitor()._states["2330"]
    assert state.seed_count == 21
    assert state.bar_count == 2


# ---------------------------------------------------------------------------
# 收盤後校正：已經完整追到的股票跳過重抓（降低全市場逐檔掃描的成本）
# ---------------------------------------------------------------------------

def test_backfill_skips_codes_whose_live_coverage_is_already_complete(monkeypatch):
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [
        {"ts": "2026-09-17", "open": 100.0, "high": 1000.0, "low": 100.0, "close": 100.0, "volume": 1},
    ])
    monkeypatch.setattr(module, "_monitor", None)
    monkeypatch.setattr(module, "STOCK_GROUPS", {"測試群組": [("2330", "台積電"), ("2317", "鴻海")]})

    complete = {"2330"}  # 2330 今天已經完整、2317 沒有（晚訂閱／有缺口）
    monkeypatch.setattr(module, "bars_5m_coverage_complete", lambda code, trade_date: code in complete)

    fetch_calls: list[str] = []

    def fake_history(code, *, calendar_days=3, service=None, hub=None, **kwargs):
        fetch_calls.append(code)
        return {"status": "ok", "bars": [bar(9, 0, 100, 102, 99, 101.5), bar(9, 5, 101.5, 103, 101, 102.5)]}

    monkeypatch.setattr(stock_history_service, "get_stock_history_bars_5m", fake_history)
    deleted: list[str] = []
    monkeypatch.setattr(module, "delete_kline_signals_for_ticker", lambda trade_date, code: deleted.append(code))

    result = module.backfill_today_kline_signals(trade_date="2026-09-18", delay=0)

    assert fetch_calls == ["2317"]  # 完整的 2330 完全沒有打歷史 API
    assert deleted == ["2317"]
    assert result["codeCount"] == 2
    assert result["codesProcessed"] == 2
    assert result["codesSkippedComplete"] == 1
    assert result["barsReplayed"] == 2  # 只有 2317 重播
    monitor = module.get_intraday_kline_signal_monitor()
    assert "2330" not in monitor._states  # 沒碰過，維持即時路徑原本算出的狀態
    assert monitor._states["2317"].bar_count == 2


def test_backfill_does_not_sleep_between_skipped_codes(monkeypatch):
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [])
    monkeypatch.setattr(module, "_monitor", None)
    monkeypatch.setattr(module, "STOCK_GROUPS", {"測試群組": [("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科")]})
    monkeypatch.setattr(module, "bars_5m_coverage_complete", lambda code, trade_date: True)

    def boom(*args, **kwargs):
        raise AssertionError("全部都完整，不該打任何歷史 API")

    monkeypatch.setattr(stock_history_service, "get_stock_history_bars_5m", boom)
    slept: list[float] = []
    monkeypatch.setattr(module.time, "sleep", lambda seconds: slept.append(seconds))

    result = module.backfill_today_kline_signals(trade_date="2026-09-18", delay=0.3)

    assert result["codesSkippedComplete"] == 3
    assert result["codesProcessed"] == 3
    assert slept == []  # 跳過的檔不打外部API，不需要延遲


def test_bars_5m_coverage_complete_used_directly(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_complete(code, trade_date):
        calls.append((code, trade_date))
        return False

    monkeypatch.setattr(module, "bars_5m_coverage_complete", fake_complete)
    monkeypatch.setattr(module, "save_intraday_signals", lambda rows: rows)
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=1: [])
    monkeypatch.setattr(module, "_monitor", None)
    monkeypatch.setattr(module, "STOCK_GROUPS", {"測試群組": [("2330", "台積電")]})
    monkeypatch.setattr(stock_history_service, "get_stock_history_bars_5m",
                         lambda code, *, calendar_days=3, service=None, hub=None, **kwargs: {"status": "ok", "bars": []})

    module.backfill_today_kline_signals(trade_date="2026-09-18", delay=0)

    assert calls == [("2330", "2026-09-18")]
