"""五分鐘K盤中訊號狀態機：905/A8/520/12空/1+2多系列。

規格來源：使用者提供文件《HANSTOCK｜策略定義備份》（整理日期2026-08-13，
以《HanStock 5分鐘K線盤中選股規則》2026/08/04版為底）。範圍限五分鐘盤中
策略；日線Rule1/Rule2不在這裡（使用者已明確指示跳過）。「1+2多」文件裡
沒有寫，使用者後續口頭補充：條件1是905高、條件2是昨日高，5分K收盤同時
站上兩者時成立，一天一次，沒有時間限制（不受長多前置條件約束）。

只認完整收盤的5分鐘K棒，跟主力副圖共用market_data_hub的5分K
aggregator，不新增任何Shioaji訂閱。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from daily_bars_store import load_daily_bars
from daytrade_flow import _tick_size
from intraday_signal_store import save_intraday_signals
from market_data_hub import BAR_INTERVAL_5M_MS
from otc_index import taipei_minute_of_day, taipei_trade_date
from stock_groups import STOCK_GROUPS

logger = logging.getLogger("hanstock.intraday_kline_signals")

LONG_PRECONDITION_MAX_PCT = 6.0  # 905收盤漲幅需<6%（相對昨收）才適用【5】/20MA上彎系列
ZONE_TICKS = 5  # 「前高下方5檔內」
WAIT_BARS = 2  # 注意12空／12空都要等2根5分K（=10分鐘）未突破
WATCH_START_MINUTE = 9 * 60 + 10  # 09:10起才開始偵測注意12空
CUTOFF_MINUTE = 10 * 60 + 30  # A8空／破905D／12空的期限

_group_lookup_cache: dict[str, tuple[str, str]] | None = None


def _group_and_name(code: str) -> tuple[str, str]:
    global _group_lookup_cache
    if _group_lookup_cache is None:
        cache: dict[str, tuple[str, str]] = {}
        for group_name, stocks in STOCK_GROUPS.items():
            for stock_code, stock_name in stocks:
                cache[str(stock_code).upper()] = (group_name, stock_name)
        _group_lookup_cache = cache
    return _group_lookup_cache.get(code, ("", code))


@dataclass
class _KlineState:
    trade_date: str = ""
    bar_count: int = 0
    closes: list[float] = field(default_factory=list)
    prev_close: float | None = None
    prev_high: float | None = None
    bar905_high: float | None = None
    bar905_low: float | None = None
    a8: float | None = None
    long_ok: bool = False
    session_high: float | None = None
    above_20ma: bool | None = None
    above_prev_high: bool = False
    above_905_5ma: bool = False
    ma20_slope: str | None = None
    prev_ma20: float | None = None
    in_520_multi: bool = False
    in_520_short: bool = False
    count_905: int = 0
    count_prev_high: int = 0
    count_20up: int = 0
    count_20down: int = 0
    fired_first_905: bool = False
    fired_first_20up: bool = False
    fired_first_20down: bool = False
    fired_combo12_bull: bool = False
    fired_a8short: bool = False
    fired_break905d: bool = False
    ever_watch12: bool = False
    watch_stage: str = "idle"
    watch_wait: int = 0


def _moving_average(closes: list[float], length: int) -> float | None:
    if len(closes) < length:
        return None
    window = closes[-length:]
    return sum(window) / length


class IntradayKlineSignalMonitor:
    def __init__(self) -> None:
        self._states: dict[str, _KlineState] = {}
        self._lock = threading.Lock()

    def _reset_for_new_day(self, code: str, trade_date: str) -> _KlineState:
        state = _KlineState(trade_date=trade_date)
        try:
            previous = load_daily_bars(code, limit=1)
        except Exception:  # noqa: BLE001
            previous = []
        if previous:
            state.prev_close = float(previous[-1]["close"])
            state.prev_high = float(previous[-1]["high"])
        self._states[code] = state
        return state

    def reset_for_backfill(self, code: str, trade_date: str) -> None:
        """歷史回補用：強制重建這檔股票在trade_date當天的狀態，避免重複
        呼叫回補（例如重試）時，因為state.trade_date沒變而誤判成「同一天
        繼續累積」，導致bar_count/closes等狀態疊加成兩天份、算出錯誤結果。
        每次回補一檔股票的完整當日bars之前，都要先呼叫這個。"""
        code = str(code).strip().upper()
        with self._lock:
            self._reset_for_new_day(code, trade_date)

    def on_bar_completed(self, code: str, bar: dict[str, Any]) -> list[dict[str, Any]]:
        code = str(code).strip().upper()
        close_ts = int(bar["ts"]) + BAR_INTERVAL_5M_MS
        trade_date = taipei_trade_date(close_ts)
        minute_of_day = taipei_minute_of_day(close_ts)

        with self._lock:
            state = self._states.get(code)
            if state is None or state.trade_date != trade_date:
                state = self._reset_for_new_day(code, trade_date)
            signals = self._process_bar(code, state, bar, close_ts, trade_date, minute_of_day)

        if signals:
            try:
                save_intraday_signals(signals)
            except Exception:  # noqa: BLE001
                logger.exception("五分鐘K訊號保存失敗 code=%s", code)
        return signals

    def _process_bar(
        self,
        code: str,
        state: _KlineState,
        bar: dict[str, Any],
        close_ts: int,
        trade_date: str,
        minute_of_day: int,
    ) -> list[dict[str, Any]]:
        close = float(bar["close"])
        high = float(bar["high"])
        state.bar_count += 1
        state.closes.append(close)

        group_name, name = _group_and_name(code)
        out: list[dict[str, Any]] = []

        def emit(kind: str, label: str, note: str = "", ma20_down: bool | None = None) -> None:
            out.append({
                "tradeDate": trade_date, "ticker": code, "name": name, "groupName": group_name,
                "kind": kind, "label": label, "barTs": close_ts, "price": close,
                "note": note, "ma20Down": ma20_down,
            })

        if state.bar_count == 1:
            # 這是當天第一根905K（09:00-09:05），只用來建立基準，不偵測訊號。
            state.bar905_high = high
            state.bar905_low = float(bar["low"])
            state.a8 = (state.bar905_high + state.bar905_low) / 2
            state.session_high = state.bar905_high
            if state.prev_close is not None and state.prev_close > 0:
                pct = (close / state.prev_close - 1) * 100
                state.long_ok = close > state.prev_close and pct < LONG_PRECONDITION_MAX_PCT
            return out

        old_session_high = state.session_high
        broke_through = old_session_high is not None and high > old_session_high
        if broke_through:
            state.session_high = high

        ma5 = _moving_average(state.closes, 5)
        ma20 = _moving_average(state.closes, 20)

        self._detect_905_cross(state, close, emit)
        self._detect_prev_high_cross(state, close, emit)
        self._detect_combo12_bull(state, close, emit)
        self._detect_20ma_cross(state, close, ma20, emit)
        self._detect_520(state, close, ma5, ma20, emit)
        self._detect_20ma_turn(state, ma20, emit)
        self._detect_a8_and_905d(state, close, minute_of_day, emit)
        self._detect_12short_family(state, close, broke_through, minute_of_day, emit)

        # 【5】需要ma5，跟其他偵測分開放在最後（依賴上面算好的ma5）。
        self._detect_5ma_905_cross(state, close, ma5, emit)

        return out

    def _detect_5ma_905_cross(self, state: _KlineState, close: float, ma5: float | None, emit) -> None:
        if state.bar905_high is None:
            return
        if state.above_905_5ma and close <= state.bar905_high:
            state.above_905_5ma = False
        if (
            state.long_ok and ma5 is not None and close > state.bar905_high
            and close > ma5 and not state.above_905_5ma
        ):
            state.count_905 += 1
            state.above_905_5ma = True
            emit("crossUp905", "第%d次站上5MA過905高" % state.count_905, f"第{state.count_905}次")

    def _detect_905_cross(self, state: _KlineState, close: float, emit) -> None:
        if state.bar905_high is None or state.fired_first_905:
            return
        if close > state.bar905_high:
            state.fired_first_905 = True
            emit("firstCross905High", "首次過905高")

    def _detect_prev_high_cross(self, state: _KlineState, close: float, emit) -> None:
        if state.prev_high is None:
            return
        if state.above_prev_high and close <= state.prev_high:
            state.above_prev_high = False
        if close > state.prev_high and not state.above_prev_high:
            state.count_prev_high += 1
            state.above_prev_high = True
            emit("crossUpPrevHigh", "第%d次站上昨日高" % state.count_prev_high, f"第{state.count_prev_high}次")

    def _detect_combo12_bull(self, state: _KlineState, close: float, emit) -> None:
        # 「1+2多」＝5分K收盤同時站上905高(條件1)跟昨日高(條件2)；不受長多
        # 前置條件限制（跟⑨/㊇一樣），一天只發一次，沒有10:30這種時間限制
        # （使用者說明：實務上大部分會在10:30前出現，但不是規則本身要求的）。
        if state.fired_combo12_bull or state.bar905_high is None or state.prev_high is None:
            return
        if close > state.bar905_high and close > state.prev_high:
            state.fired_combo12_bull = True
            emit("combo12Bull", "1+2多")

    def _detect_20ma_cross(self, state: _KlineState, close: float, ma20: float | None, emit) -> None:
        if ma20 is None:
            return
        is_above = close > ma20
        if state.above_20ma is None:
            state.above_20ma = is_above
            return
        if not state.above_20ma and is_above:
            if state.long_ok:
                state.count_20up += 1
                emit("crossUp20ma", "第%d次站上20MA" % state.count_20up, f"第{state.count_20up}次")
                if not state.fired_first_20up:
                    state.fired_first_20up = True
                    emit("firstCrossUp20ma", "首次站上20MA")
        elif state.above_20ma and not is_above:
            state.count_20down += 1
            emit("crossDown20ma", "第%d次跌破20MA" % state.count_20down, f"第{state.count_20down}次")
            if not state.fired_first_20down:
                state.fired_first_20down = True
                emit("firstCrossDown20ma", "首次跌破20MA")
            if state.ever_watch12:
                emit("enhanced12short", "加強12空", "注意12空後再跌破20MA", ma20_down=True)
        state.above_20ma = is_above

    def _detect_520(self, state: _KlineState, close: float, ma5: float | None, ma20: float | None, emit) -> None:
        if ma5 is None or ma20 is None:
            return
        is_multi = close > ma5 and close > ma20
        if is_multi and not state.in_520_multi:
            emit("ma520Up", "五二零上")
        state.in_520_multi = is_multi
        is_short = close < ma5 and close < ma20
        if is_short and not state.in_520_short:
            emit("ma520Down", "五二零下")
        state.in_520_short = is_short

    def _detect_20ma_turn(self, state: _KlineState, ma20: float | None, emit) -> None:
        if ma20 is None:
            return
        if state.prev_ma20 is not None:
            if ma20 > state.prev_ma20:
                new_slope = "up"
            elif ma20 < state.prev_ma20:
                new_slope = "down"
            else:
                new_slope = state.ma20_slope
            if state.ma20_slope is not None and new_slope in ("up", "down") and new_slope != state.ma20_slope:
                if new_slope == "up":
                    emit("ma20turnUp", "20MA轉上彎")
                else:
                    emit("ma20turnDown", "20MA轉下彎")
            state.ma20_slope = new_slope
        state.prev_ma20 = ma20

    def _detect_a8_and_905d(self, state: _KlineState, close: float, minute_of_day: int, emit) -> None:
        if minute_of_day >= CUTOFF_MINUTE:
            return
        if not state.fired_a8short and state.a8 is not None and close < state.a8:
            state.fired_a8short = True
            emit("a8short", "A8空")
        if not state.fired_break905d and state.bar905_low is not None and close < state.bar905_low:
            state.fired_break905d = True
            emit("break905d", "破905D")

    def _detect_12short_family(
        self, state: _KlineState, close: float, broke_through: bool, minute_of_day: int, emit
    ) -> None:
        if state.session_high is None:
            return
        tick = _tick_size(state.session_high)
        zone_lower = state.session_high - ZONE_TICKS * tick
        in_zone = zone_lower <= close < state.session_high

        if broke_through and state.watch_stage == "entering":
            # 判定注意12空期間突破：作廢，前高已經在上層更新，重新偵測。
            state.watch_stage = "idle"
            state.watch_wait = 0
            return
        if broke_through and state.watch_stage == "entering2":
            # 判定12空期間突破：不整段作廢（注意12空已經成立過），回到
            # 「等待離開/更新前高」，等下一次回到新前高5檔內再重新判定12空。
            state.watch_stage = "confirmed_watching_exit"
            state.watch_wait = 0
            return

        if state.watch_stage == "idle":
            if WATCH_START_MINUTE <= minute_of_day < CUTOFF_MINUTE and in_zone:
                state.watch_stage = "entering"
                state.watch_wait = 0
        elif state.watch_stage == "entering":
            state.watch_wait += 1
            if state.watch_wait >= WAIT_BARS:
                state.watch_stage = "confirmed_watching_exit"
                state.ever_watch12 = True
                emit("watch12short", "注意12空")
        elif state.watch_stage == "confirmed_watching_exit":
            # 離開條件：跌出5檔區域，或突破前高（上面broke_through那個分支已經
            # 處理過entering2的突破；這裡處理注意12空成立後、還沒進入entering2
            # 前，區域外的任何一根新高一樣算「離開」）。
            if not in_zone or broke_through:
                state.watch_stage = "idle2_armed"
        elif state.watch_stage == "idle2_armed":
            if minute_of_day < CUTOFF_MINUTE and in_zone:
                state.watch_stage = "entering2"
                state.watch_wait = 0
        elif state.watch_stage == "entering2":
            state.watch_wait += 1
            if state.watch_wait >= WAIT_BARS:
                state.watch_stage = "done"
                if minute_of_day < CUTOFF_MINUTE:
                    emit("short12", "12空")


_monitor: IntradayKlineSignalMonitor | None = None
_monitor_lock = threading.Lock()


def get_intraday_kline_signal_monitor() -> IntradayKlineSignalMonitor:
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = IntradayKlineSignalMonitor()
    return _monitor


def backfill_today_kline_signals(
    *, service: Any = None, hub: Any = None, trade_date: str | None = None, delay: float = 0.3,
) -> dict[str, Any]:
    """一次性回補：用Shioaji歷史kbars重播trade_date當天已經走完的5分K，
    補回「偵測引擎當天收盤後才上線」這段時間本來會漏掉的訊號。

    只回補main_force_bars今天已經有資料的股票（代表確實被追蹤/訂閱過，
    歷史查詢比較可靠），不是全市場654檔都補，避免耗用過多Shioaji歷史
    資料配額。重播前一律先reset_for_backfill，讓重複執行本身是安全、
    冪等的（DB層的ONCE_PER_DAY/UNIQUE也會再擋一次重複寫入）。"""
    from datetime import datetime

    from main_force_store import list_tracked_stock_codes
    from otc_index import TW_TZ
    from stock_history_service import get_stock_history_bars_5m

    trade_date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    codes = list_tracked_stock_codes(trade_date, interval="1m")
    monitor = get_intraday_kline_signal_monitor()
    processed = 0
    bars_replayed = 0
    signals_emitted = 0
    failures: list[dict[str, str]] = []
    for code in codes:
        try:
            result = get_stock_history_bars_5m(code, calendar_days=3, service=service, hub=hub)
            todays_bars = sorted(
                (b for b in result.get("bars", []) if taipei_trade_date(int(b["ts"])) == trade_date),
                key=lambda b: b["ts"],
            )
            monitor.reset_for_backfill(code, trade_date)
            for bar in todays_bars:
                emitted = monitor.on_bar_completed(code, bar)
                bars_replayed += 1
                signals_emitted += len(emitted)
            processed += 1
        except Exception as error:  # noqa: BLE001
            failures.append({"code": code, "error": str(error)})
            logger.exception("五分鐘K訊號回補失敗 code=%s", code)
        time.sleep(max(0.0, delay))
    return {
        "tradeDate": trade_date,
        "codeCount": len(codes),
        "codesProcessed": processed,
        "barsReplayed": bars_replayed,
        "signalsEmitted": signals_emitted,
        "failures": failures,
    }


_backfill_status: dict[str, Any] = {"running": False, "result": None}
_backfill_status_lock = threading.Lock()


def kline_signal_backfill_status() -> dict[str, Any]:
    with _backfill_status_lock:
        return dict(_backfill_status)


def start_kline_signal_backfill_today() -> dict[str, Any]:
    """背景執行緒觸發一次性回補；已經在跑就不會重複啟動。"""
    with _backfill_status_lock:
        if _backfill_status["running"]:
            return {"started": False, "reason": "already_running"}
        _backfill_status["running"] = True
        _backfill_status["result"] = None

    def _run() -> None:
        try:
            result = backfill_today_kline_signals()
        except Exception as error:  # noqa: BLE001
            result = {"error": str(error)}
            logger.exception("五分鐘K訊號回補整體失敗")
        with _backfill_status_lock:
            _backfill_status["running"] = False
            _backfill_status["result"] = result

    threading.Thread(target=_run, name="hanstock-kline-signal-backfill", daemon=True).start()
    return {"started": True}
