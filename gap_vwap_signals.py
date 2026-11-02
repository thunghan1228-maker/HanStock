"""開低站上 VWAP(88)／開高跌破 VWAP(87)：每根 1 分 K 收完就判斷（2026-11-02 使用者）。

88（多）：今天開盤價 < 昨收（開低），而這根 1 分 K 收盤 > 當下的 VWAP（今日均價）。
87（空）：今天開盤價 > 昨收（開高），而這根 1 分 K 收盤 < 當下的 VWAP。
開平盤（開盤＝昨收）兩個都不算。每檔每天各發一次，時間＝第一次符合的那根 1 分 K 收盤。

即時路徑跟主力副圖共用 market_data_hub 的 1 分 K aggregator，不新增任何 Shioaji 訂閱；
開盤價、昨收、VWAP 優先用 Shioaji tick 本身帶的當日 open／price_chg／avg_price（較晚才
訂閱到的股票也準），拿不到才用第一根 1 分 K 的開盤、日K昨收、訂閱起算的收盤×量近似。
收盤後（13:40 起）用當天完整 kbars 重播一次（gap_vwap_backfill_collector），以重播結果為準。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from daily_bars_store import load_daily_bars
from intraday_signal_store import delete_signals_for_ticker, save_intraday_signals
from main_force_store import list_main_force_codes_for_date
from otc_index import taipei_minute_of_day, taipei_trade_date
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS, industry_group_codes
from stock_history_service import get_stock_history_bars_1m

logger = logging.getLogger("hanstock.gap_vwap_signals")
TW_TZ = timezone(timedelta(hours=8))
ONE_MIN_MS = 60_000
OPEN_MINUTE = 9 * 60
# 判定規則版本：規則一改，收盤後重播排程會把已完成的日期再重播一次。
GAP_VWAP_RULES_VERSION = 1
KIND_BULL = "gapDownAboveVwap"   # 機器人代碼 88
KIND_BEAR = "gapUpBelowVwap"     # 機器人代碼 87
GAP_VWAP_SIGNAL_KINDS = {KIND_BULL, KIND_BEAR}
LABEL_BULL = "88 開低站上VWAP"
LABEL_BEAR = "87 開高跌破VWAP"

_group_lookup_cache: dict[str, tuple[str, str]] | None = None


def _group_and_name(code: str) -> tuple[str, str]:
    global _group_lookup_cache
    if _group_lookup_cache is None:
        cache: dict[str, tuple[str, str]] = {}
        for group_name, stocks in STOCK_GROUPS.items():  # 跟主力翻多空一致：以第一個出現的一般族群為準
            if group_name in SPECIAL_GROUP_NAMES:
                continue
            for stock_code, stock_name in stocks:
                cache.setdefault(str(stock_code).upper(), (group_name, stock_name))
        _group_lookup_cache = cache
    return _group_lookup_cache.get(code, ("", code))


def _pos(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _hhmm(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, TW_TZ).strftime("%H:%M")


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


@dataclass
class _GapState:
    trade_date: str
    prev_close: float | None = None
    day_open: float | None = None
    cum_volume: int = 0
    cum_close_volume: float = 0.0
    fired_bull: bool = False
    fired_bear: bool = False


def _prev_close_from_daily(code: str, trade_date: str) -> float | None:
    try:
        daily = load_daily_bars(code, limit=6)
    except Exception:  # noqa: BLE001
        return None
    for bar in reversed(daily):
        if str(bar.get("ts", ""))[:10] < trade_date:
            return _pos(bar.get("close"))
    return None


class GapVwapMonitor:
    def __init__(self, tick_lookup: Callable[[str], dict[str, Any] | None] | None = None) -> None:
        self._states: dict[str, _GapState] = {}
        self._lock = threading.Lock()
        self._tick_lookup = tick_lookup
        self._bars_processed = 0
        self._fired = {"bull": 0, "bear": 0}
        self._last_bar_at: str | None = None

    def status(self) -> dict[str, Any]:
        today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
        with self._lock:
            return {
                "trackedCodes": sum(1 for state in self._states.values() if state.trade_date == today),
                "barsProcessed": self._bars_processed,
                "firedBull88": self._fired["bull"],
                "firedBear87": self._fired["bear"],
                "lastBarAt": self._last_bar_at,
                "rulesVersion": GAP_VWAP_RULES_VERSION,
            }

    def _new_state(self, code: str, trade_date: str, prev_close: float | None = None) -> _GapState:
        state = _GapState(trade_date=trade_date, prev_close=prev_close or _prev_close_from_daily(code, trade_date))
        self._states[code] = state
        return state

    def reset_for_backfill(self, code: str, trade_date: str, prev_close: float | None = None) -> None:
        with self._lock:
            self._new_state(str(code).strip().upper(), trade_date, prev_close)

    def _tick(self, code: str) -> dict[str, Any] | None:
        if self._tick_lookup is None:
            return None
        try:
            return self._tick_lookup(code)
        except Exception:  # noqa: BLE001
            return None

    def on_bar_completed(
        self, code: str, bar: dict[str, Any], *, persist: bool = True, use_tick: bool = True,
    ) -> list[dict[str, Any]]:
        code = str(code).strip().upper()
        try:
            bar_ts = int(bar["ts"])
            close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            return []
        if close <= 0:
            return []
        close_ts = bar_ts + ONE_MIN_MS
        trade_date = taipei_trade_date(close_ts)
        tick = self._tick(code) if use_tick else None
        with self._lock:
            state = self._states.get(code)
            if state is None or state.trade_date != trade_date:
                state = self._new_state(code, trade_date)
            signals = self._process_bar(code, state, bar, bar_ts, close, close_ts, trade_date, tick)
            self._bars_processed += 1
            self._last_bar_at = datetime.fromtimestamp(close_ts / 1000, TW_TZ).isoformat()
            for signal in signals:
                self._fired["bull" if signal["kind"] == KIND_BULL else "bear"] += 1
        if signals and persist:
            try:
                save_intraday_signals(signals)
            except Exception:  # noqa: BLE001
                logger.exception("88/87 開低開高VWAP訊號保存失敗 code=%s", code)
        return signals

    def _process_bar(
        self,
        code: str,
        state: _GapState,
        bar: dict[str, Any],
        bar_ts: int,
        close: float,
        close_ts: int,
        trade_date: str,
        tick: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        try:
            volume = max(0, int(bar.get("volume") or 0))
        except (TypeError, ValueError):
            volume = 0
        state.cum_volume += volume
        state.cum_close_volume += close * volume

        # 開盤價：tick 帶的當日開盤最準；沒有的話，從 09:00 第一根就看到的股票用那根的開盤價。
        tick_open = _pos((tick or {}).get("open"))
        if tick_open:
            state.day_open = tick_open
        elif state.day_open is None and taipei_minute_of_day(bar_ts) <= OPEN_MINUTE:
            state.day_open = _pos(bar.get("open"))
        # 昨收：日K優先；沒有就用 tick 的 close − price_chg 反推。
        if state.prev_close is None and tick:
            tick_close, chg = _pos(tick.get("close")), tick.get("price_chg")
            try:
                if tick_close and chg is not None:
                    state.prev_close = _pos(tick_close - float(chg))
            except (TypeError, ValueError):
                pass
        # VWAP：tick 的 avg_price（Shioaji 當日均價）＞ 當日累計成交金額÷成交量 ＞ 收盤×量近似。
        vwap = _pos((tick or {}).get("avg_price"))
        if vwap is None:
            total_amount, total_volume = _pos(bar.get("total_amount")), _pos(bar.get("total_volume"))
            if total_amount and total_volume:
                vwap = total_amount / (total_volume * 1000.0)
            elif state.cum_volume > 0:
                vwap = state.cum_close_volume / state.cum_volume
        if not vwap or not state.day_open or not state.prev_close:
            return []

        gap_pct = (state.day_open / state.prev_close - 1) * 100
        distance_pct = (close / vwap - 1) * 100
        group_name, name = _group_and_name(code)
        common = {
            "tradeDate": trade_date, "ticker": code, "name": name, "groupName": group_name,
            "barTs": close_ts, "price": close,
            "note": (
                f"開盤 {_fmt(state.day_open)}｜昨收 {_fmt(state.prev_close)}（{gap_pct:+.2f}%）"
                f"｜VWAP {_fmt(vwap)}｜距VWAP {distance_pct:+.2f}%"
            ),
        }
        signals: list[dict[str, Any]] = []
        if not state.fired_bull and state.day_open < state.prev_close and close > vwap:
            state.fired_bull = True
            signals.append({**common, "kind": KIND_BULL, "label": LABEL_BULL})
        if not state.fired_bear and state.day_open > state.prev_close and close < vwap:
            state.fired_bear = True
            signals.append({**common, "kind": KIND_BEAR, "label": LABEL_BEAR})
        return signals


_monitor: GapVwapMonitor | None = None


def _hub_tick(code: str) -> dict[str, Any] | None:
    from market_data_hub import get_market_data_hub
    return get_market_data_hub().get_tick(code)


def get_gap_vwap_monitor() -> GapVwapMonitor:
    global _monitor
    if _monitor is None:
        _monitor = GapVwapMonitor(tick_lookup=_hub_tick)
    return _monitor


def _prev_close_from_history(bars: list[dict[str, Any]], trade_date: str) -> float | None:
    earlier = [bar for bar in bars if taipei_trade_date(int(bar["ts"])) < trade_date]
    return _pos(max(earlier, key=lambda bar: int(bar["ts"]))["close"]) if earlier else None


def backfill_gap_vwap_signals(
    trade_date: str,
    *,
    service: Any = None,
    hub: Any = None,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    """用 trade_date 當天完整的 1 分 K（09:00 起）重播 88／87，覆蓋即時路徑的結果。

    只重播當天有主力副圖資料（＝當天被訂閱到、有 tick）的族群股票；跟主力翻多空回補共用
    同一份多日 kbars 快取。開盤價＝當天第一根 1 分 K 的開盤、VWAP＝收盤×量累計，昨收＝日K
    （沒有就用前一天最後一根 1 分 K 收盤）。只有真的拿到當天 kbars 才刪除重寫。"""
    target = codes if codes is not None else list_main_force_codes_for_date(trade_date, "1m")
    group_codes = industry_group_codes()
    target_codes = sorted(code for code in {str(code).strip().upper() for code in target} if code in group_codes)
    monitor = get_gap_vwap_monitor()
    processed = bars_replayed = signals_emitted = skipped_no_bars = 0
    failures: list[dict[str, str]] = []
    quota_blocked = False
    for code in target_codes:
        try:
            history = get_stock_history_bars_1m(code, calendar_days=5, service=service, hub=hub)
            error = history.get("error")
            if error and "history_quota_exhausted" in str(error):
                quota_blocked = True
                failures.append({"code": code, "error": str(error)})
                break
            bars = sorted(history.get("bars", []), key=lambda bar: int(bar["ts"]))
            day_bars = [bar for bar in bars if taipei_trade_date(int(bar["ts"])) == trade_date]
            if not day_bars or taipei_minute_of_day(int(day_bars[0]["ts"])) > OPEN_MINUTE:
                skipped_no_bars += 1  # 沒有當天、或缺 09:00 第一根就不知道開盤價，不重寫
                if error:
                    failures.append({"code": code, "error": str(error)})
                continue
            delete_signals_for_ticker(trade_date, code, GAP_VWAP_SIGNAL_KINDS)
            monitor.reset_for_backfill(
                code, trade_date, _prev_close_from_daily(code, trade_date) or _prev_close_from_history(bars, trade_date)
            )
            for bar in day_bars:
                signals_emitted += len(monitor.on_bar_completed(code, bar, use_tick=False))
            bars_replayed += len(day_bars)
            processed += 1
        except Exception as error:  # noqa: BLE001
            failures.append({"code": code, "error": f"{type(error).__name__}: {error}"})
    return {
        "tradeDate": trade_date, "codeCount": len(target_codes), "processed": processed,
        "barsReplayed": bars_replayed, "signalsEmitted": signals_emitted, "skippedNoBars": skipped_no_bars,
        "quotaBlocked": quota_blocked, "failureCount": len(failures), "failures": failures[:50],
    }


_backfill_status_lock = threading.Lock()
_backfill_status: dict[str, Any] = {"running": False, "tradeDate": None, "result": None}


def gap_vwap_backfill_status() -> dict[str, Any]:
    with _backfill_status_lock:
        return dict(_backfill_status)


def start_gap_vwap_backfill(trade_date: str | None = None) -> dict[str, Any]:
    """背景執行緒觸發一次重播回補；已經在跑就不重複啟動。trade_date 預設今天。"""
    trade_date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    with _backfill_status_lock:
        if _backfill_status["running"]:
            return {"started": False, "reason": "already_running", "tradeDate": _backfill_status["tradeDate"]}
        _backfill_status.update({"running": True, "tradeDate": trade_date, "result": None})

    def _run() -> None:
        try:
            result = backfill_gap_vwap_signals(trade_date)
        except Exception as error:  # noqa: BLE001
            result = {"tradeDate": trade_date, "error": f"{type(error).__name__}: {error}"}
            logger.exception("88/87 回補整體失敗 trade_date=%s", trade_date)
        with _backfill_status_lock:
            _backfill_status.update({"running": False, "result": result})
        logger.info("88/87 開低開高VWAP回補完成: %s", result)

    threading.Thread(target=_run, name="hanstock-gap-vwap-backfill", daemon=True).start()
    return {"started": True, "tradeDate": trade_date}
