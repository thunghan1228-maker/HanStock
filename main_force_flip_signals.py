"""主力累計翻多／翻空（A～D 同步濾網）：每根 1 分 K 收完就評估。

對齊使用者另一台工具的「主力累計強勢翻多」（全市場掃描・主力累計）：
A. 主力零軸：當日主力累計淨額（大單買張−大單賣張）由 ≤0 翻到 >0（翻空反向）。
B. VWAP 穿越：1 分 K 收盤由 ≤VWAP 站上 >VWAP（翻空反向）。
C. 主力淨額率：累計淨額 ÷ 累計大單總張數 ≥ NET_RATIO_MIN（強勢 ≥ NET_RATIO_STRONG）。
D. 量比：今日累計成交量換算成整天速度 ÷ 前 5 個交易日平均成交量 ≥ VOLUME_RATIO_MIN
   （強勢 ≥ VOLUME_RATIO_STRONG）。
A、B 兩次穿越都要在 SYNC_WINDOW 內發生、評估當下仍成立，C、D 同時達標才發；
C、D 都達強勢門檻標「強勢」。每檔每天多空各一次。跟主力副圖共用 market_data_hub
的 1 分 K aggregator，不新增任何 Shioaji 訂閱。

即時路徑的股票訂閱是動態、有上限的：某檔較晚才被訂閱到時，累計值只從訂閱起算，
所以至少要看過 MIN_BARS 根 1 分 K 才允許發訊號，避免剛訂閱的前幾根被誤判成零軸。
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from daily_bars_store import load_daily_bars
from intraday_signal_store import save_intraday_signals
from otc_index import taipei_minute_of_day, taipei_trade_date
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

logger = logging.getLogger("hanstock.main_force_flip_signals")
TW_TZ = timezone(timedelta(hours=8))
ONE_MIN_MS = 60_000
SESSION_MINUTES = 270  # 09:00~13:30

NET_RATIO_MIN = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_NET_RATIO_MIN", "0.20"))
NET_RATIO_STRONG = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_NET_RATIO_STRONG", "0.40"))
VOLUME_RATIO_MIN = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_VOLUME_RATIO_MIN", "1.5"))
VOLUME_RATIO_STRONG = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_VOLUME_RATIO_STRONG", "3.0"))
SYNC_WINDOW_MS = max(1, int(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_SYNC_WINDOW_MINUTES", "5"))) * ONE_MIN_MS
MIN_MAIN_GROSS_LOTS = max(1, int(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_MIN_MAIN_LOTS", "30")))
MIN_BARS = max(1, int(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_MIN_BARS", "10")))
MAX_VWAP_DISTANCE_PCT = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_MAX_VWAP_DISTANCE_PCT", "3.0"))
KIND_BULL = "mainForceFlipBull"
KIND_BEAR = "mainForceFlipBear"

_group_lookup_cache: dict[str, tuple[str, str]] | None = None


def _group_and_name(code: str) -> tuple[str, str]:
    global _group_lookup_cache
    if _group_lookup_cache is None:
        cache: dict[str, tuple[str, str]] = {}
        # 股期標的是特殊清單不是族群，且排在最後；一檔股票以它第一個出現的一般族群為準。
        for group_name, stocks in STOCK_GROUPS.items():
            if group_name in SPECIAL_GROUP_NAMES:
                continue
            for stock_code, stock_name in stocks:
                cache.setdefault(str(stock_code).upper(), (group_name, stock_name))
        _group_lookup_cache = cache
    return _group_lookup_cache.get(code, ("", code))


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _hhmm(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, TW_TZ).strftime("%H:%M")


@dataclass
class _FlipState:
    trade_date: str
    bar_count: int = 0
    cum_net: int = 0
    cum_gross: int = 0
    cum_volume: int = 0
    cum_close_volume: float = 0.0
    total_amount: float = 0.0
    total_volume: int = 0
    avg_daily_volume: float | None = None
    prev_above_vwap: bool | None = None
    bull_zero_cross_ts: int | None = None
    bear_zero_cross_ts: int | None = None
    bull_vwap_cross_ts: int | None = None
    bear_vwap_cross_ts: int | None = None
    fired_bull: bool = False
    fired_bear: bool = False


def _note(zero_ts: int, vwap_ts: int, net_ratio: float, distance_pct: float, volume_ratio: float, cum_net: int) -> str:
    return (
        f"A～D同步濾網｜主力零軸 {_hhmm(zero_ts)}｜VWAP穿越 {_hhmm(vwap_ts)}"
        f"｜主力淨額率 {net_ratio * 100:+.2f}%｜距VWAP {distance_pct:+.2f}%"
        f"｜量比 {volume_ratio:.2f}×｜累計 {cum_net:+d} 張"
    )


class MainForceFlipMonitor:
    def __init__(self) -> None:
        self._states: dict[str, _FlipState] = {}
        self._lock = threading.Lock()

    def _reset_for_new_day(self, code: str, trade_date: str) -> _FlipState:
        state = _FlipState(trade_date=trade_date)
        try:
            daily = load_daily_bars(code, limit=6)
        except Exception:  # noqa: BLE001
            daily = []
        volumes = [
            float(bar["volume"]) for bar in daily
            if str(bar.get("ts", ""))[:10] != trade_date and _float(bar.get("volume")) > 0
        ][-5:]
        if volumes:
            state.avg_daily_volume = sum(volumes) / len(volumes)
        self._states[code] = state
        return state

    def reset_for_backfill(self, code: str, trade_date: str) -> None:
        with self._lock:
            self._reset_for_new_day(str(code).strip().upper(), trade_date)

    def on_bar_completed(self, code: str, bar: dict[str, Any]) -> list[dict[str, Any]]:
        code = str(code).strip().upper()
        try:
            close_ts = int(bar["ts"]) + ONE_MIN_MS
            close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            return []
        if close <= 0:
            return []
        trade_date = taipei_trade_date(close_ts)
        with self._lock:
            state = self._states.get(code)
            if state is None or state.trade_date != trade_date:
                state = self._reset_for_new_day(code, trade_date)
            signals = self._process_bar(code, state, bar, close, close_ts, trade_date)
        if signals:
            try:
                save_intraday_signals(signals)
            except Exception:  # noqa: BLE001
                logger.exception("主力累計翻多空訊號保存失敗 code=%s", code)
        return signals

    @staticmethod
    def _vwap(state: _FlipState) -> float | None:
        # tick 自帶的今日累計成交金額/成交量最準（不受訂閱起點影響）；沒有就退回
        # 用訂閱起算的 1 分 K 收盤×量近似。
        if state.total_amount > 0 and state.total_volume > 0:
            return state.total_amount / (state.total_volume * 1000.0)
        if state.cum_volume > 0:
            return state.cum_close_volume / state.cum_volume
        return None

    @staticmethod
    def _volume_ratio(state: _FlipState, close_ts: int) -> float | None:
        if not state.avg_daily_volume:
            return None
        elapsed = min(SESSION_MINUTES, max(1, taipei_minute_of_day(close_ts) - 9 * 60))
        today_volume = state.total_volume if state.total_volume > 0 else state.cum_volume
        if today_volume <= 0:
            return None
        return today_volume * SESSION_MINUTES / elapsed / state.avg_daily_volume

    def _process_bar(
        self,
        code: str,
        state: _FlipState,
        bar: dict[str, Any],
        close: float,
        close_ts: int,
        trade_date: str,
    ) -> list[dict[str, Any]]:
        volume = _int(bar.get("volume"))
        main_buy = _int(bar.get("main_buy_volume"))
        main_sell = _int(bar.get("main_sell_volume"))
        before_net = state.cum_net
        state.cum_net += main_buy - main_sell
        state.cum_gross += main_buy + main_sell
        state.cum_volume += volume
        state.cum_close_volume += close * volume
        total_amount = _float(bar.get("total_amount"))
        total_volume = _int(bar.get("total_volume"))
        if total_amount > state.total_amount:
            state.total_amount = total_amount
        if total_volume > state.total_volume:
            state.total_volume = total_volume
        state.bar_count += 1

        vwap = self._vwap(state)
        if vwap is None or vwap <= 0:
            return []
        above = close > vwap
        if state.prev_above_vwap is not None:
            if above and not state.prev_above_vwap:
                state.bull_vwap_cross_ts = close_ts
            elif not above and state.prev_above_vwap:
                state.bear_vwap_cross_ts = close_ts
        state.prev_above_vwap = above

        sign_before, sign_after = _sign(before_net), _sign(state.cum_net)
        if sign_after > 0 and sign_before <= 0:
            state.bull_zero_cross_ts = close_ts
        if sign_after < 0 and sign_before >= 0:
            state.bear_zero_cross_ts = close_ts

        if state.bar_count < MIN_BARS or state.cum_gross < MIN_MAIN_GROSS_LOTS:
            return []
        volume_ratio = self._volume_ratio(state, close_ts)
        if volume_ratio is None:
            return []
        net_ratio = state.cum_net / state.cum_gross
        distance_pct = (close / vwap - 1) * 100
        window_start = close_ts - SYNC_WINDOW_MS
        group_name, name = _group_and_name(code)
        common = {
            "tradeDate": trade_date, "ticker": code, "name": name, "groupName": group_name,
            "barTs": close_ts, "price": close,
        }
        signals: list[dict[str, Any]] = []

        if (
            not state.fired_bull and state.cum_net > 0 and above
            and state.bull_zero_cross_ts is not None and state.bull_zero_cross_ts >= window_start
            and state.bull_vwap_cross_ts is not None and state.bull_vwap_cross_ts >= window_start
            and net_ratio >= NET_RATIO_MIN and volume_ratio >= VOLUME_RATIO_MIN
            and 0 <= distance_pct <= MAX_VWAP_DISTANCE_PCT
        ):
            strong = net_ratio >= NET_RATIO_STRONG and volume_ratio >= VOLUME_RATIO_STRONG
            state.fired_bull = True
            signals.append({
                **common, "kind": KIND_BULL,
                "label": "主力累計強勢翻多" if strong else "主力累計翻多",
                "note": _note(state.bull_zero_cross_ts, state.bull_vwap_cross_ts, net_ratio, distance_pct, volume_ratio, state.cum_net),
            })

        if (
            not state.fired_bear and state.cum_net < 0 and not above
            and state.bear_zero_cross_ts is not None and state.bear_zero_cross_ts >= window_start
            and state.bear_vwap_cross_ts is not None and state.bear_vwap_cross_ts >= window_start
            and net_ratio <= -NET_RATIO_MIN and volume_ratio >= VOLUME_RATIO_MIN
            and -MAX_VWAP_DISTANCE_PCT <= distance_pct <= 0
        ):
            strong = net_ratio <= -NET_RATIO_STRONG and volume_ratio >= VOLUME_RATIO_STRONG
            state.fired_bear = True
            signals.append({
                **common, "kind": KIND_BEAR,
                "label": "主力累計強勢翻空" if strong else "主力累計翻空",
                "note": _note(state.bear_zero_cross_ts, state.bear_vwap_cross_ts, net_ratio, distance_pct, volume_ratio, state.cum_net),
            })
        return signals


_monitor: MainForceFlipMonitor | None = None


def get_main_force_flip_monitor() -> MainForceFlipMonitor:
    global _monitor
    if _monitor is None:
        _monitor = MainForceFlipMonitor()
    return _monitor
