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
from intraday_signal_store import delete_signals_for_ticker, save_intraday_signals
from main_force_store import list_main_force_codes_for_date, load_main_force_bars
from otc_index import taipei_minute_of_day, taipei_trade_date
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS
from stock_history_service import get_stock_history_bars_1m

logger = logging.getLogger("hanstock.main_force_flip_signals")
TW_TZ = timezone(timedelta(hours=8))
ONE_MIN_MS = 60_000
SESSION_MINUTES = 270  # 09:00~13:30

# 門檻由另一台工具 2026-09-22 的 14 筆實際訊號反推：淨額率最低 +5.87%／-5.94%、量比最低 1.85×、
# 距 VWAP 都在 ±1% 內，而且 14 筆全部標「強勢」（沒有分強弱）。強勢門檻預設等於基本門檻，
# 只要觸發就是「主力累計強勢翻多／翻空」；要分強弱再用環境變數把 STRONG 調高。
NET_RATIO_MIN = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_NET_RATIO_MIN", "0.05"))
NET_RATIO_STRONG = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_NET_RATIO_STRONG", "") or NET_RATIO_MIN)
VOLUME_RATIO_MIN = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_VOLUME_RATIO_MIN", "1.5"))
VOLUME_RATIO_STRONG = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_VOLUME_RATIO_STRONG", "") or VOLUME_RATIO_MIN)
SYNC_WINDOW_MS = max(1, int(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_SYNC_WINDOW_MINUTES", "5"))) * ONE_MIN_MS
# 主力買賣合計至少這麼多張才判定（30 張只是 1.5 筆大單，2026-09-23 早盤一堆累計 ±3～30 張的雜訊）。
MIN_MAIN_GROSS_LOTS = max(1, int(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_MIN_MAIN_LOTS", "100")))
# 翻過去之後累計淨額至少要有一筆大單的份量（主力大單門檻 20 張），不然只是買賣互相抵銷的零頭。
MIN_CUM_NET_LOTS = max(0, int(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_MIN_NET_LOTS", "20")))
MIN_BARS = max(1, int(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_MIN_BARS", "10")))
# 從開盤第一根就看到的股票，前幾根只是開盤集合競價的餘波：另一台工具最早的訊號是 09:05，
# 這裡前 4 根（收盤 09:01～09:04）不判定、09:05 起才看（較晚才訂閱到的仍用 MIN_BARS）。
OPEN_SKIP_BARS = max(0, int(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_OPEN_SKIP_BARS", "4")))
# 判定規則版本：規則一改，收盤後重播排程就會把已標記完成的日期再重播一次，不然舊規則漏掉的
# 訊號永遠補不回來。v2：從開盤第一根就看到的股票不暖機 10 根。v3：開盤前幾根不判定、主力買賣
# 兩邊都要有過量才算「翻」。v4：照另一台工具的 14 筆實際訊號校準——門檻降到 5%／1.5×／1%、
# 一律標強勢、09:05 起判定、累計反向再翻回來可以再發（同一檔一天不只一次）。v5：「翻」一定要從
# 另一邊翻過來——開盤後累計從 0 走出的第一個方向不算零軸穿越（2026-09-23 09:05 一口氣發了 25 筆
# 都是這種，另一台工具 3532 的 09:01 從 0 翻正也沒發、11:38 真的從負翻正才發）；主力合計 30→100 張、
# 累計淨額至少 ±20 張。
FLIP_RULES_VERSION = 5
MAX_VWAP_DISTANCE_PCT = float(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_MAX_VWAP_DISTANCE_PCT", "3.0"))
KIND_BULL = "mainForceFlipBull"
KIND_BEAR = "mainForceFlipBear"
FLIP_SIGNAL_KINDS = {KIND_BULL, KIND_BEAR}

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
    warmup_bars: int = MIN_BARS
    cum_net: int = 0
    cum_gross: int = 0
    cum_buy: int = 0
    cum_sell: int = 0
    cum_volume: int = 0
    cum_close_volume: float = 0.0
    total_amount: float = 0.0
    total_volume: int = 0
    avg_daily_volume: float | None = None
    prev_above_vwap: bool | None = None
    last_sign: int = 0  # 累計淨額最近一次非零的正負；0 = 還沒有任何主力量
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
        self._bars_processed = 0
        self._fired = {"bull": 0, "bear": 0}
        self._last_bar_at: str | None = None
        self._trace: list[dict[str, Any]] | None = None

    def enable_trace(self) -> None:
        """單檔檢查用：每根 1 分 K 記下累計、VWAP、量比，以及同步視窗內被哪個濾網擋下。"""
        self._trace = []

    def trace(self) -> list[dict[str, Any]]:
        return list(self._trace or [])

    def status(self) -> dict[str, Any]:
        """給 /api/hub/persistence/status 看的運作狀態：開盤後 barsProcessed 有在漲
        就代表偵測器在跑，不用等到真的有訊號才能確認。"""
        today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
        with self._lock:
            tracked = sum(1 for state in self._states.values() if state.trade_date == today)
            return {
                "trackedCodes": tracked,
                "barsProcessed": self._bars_processed,
                "firedBull": self._fired["bull"],
                "firedBear": self._fired["bear"],
                "lastBarAt": self._last_bar_at,
                "thresholds": {
                    "rulesVersion": FLIP_RULES_VERSION,
                    "netRatioMin": NET_RATIO_MIN, "netRatioStrong": NET_RATIO_STRONG,
                    "volumeRatioMin": VOLUME_RATIO_MIN, "volumeRatioStrong": VOLUME_RATIO_STRONG,
                    "syncWindowMinutes": SYNC_WINDOW_MS // ONE_MIN_MS, "minMainLots": MIN_MAIN_GROSS_LOTS,
                    "minNetLots": MIN_CUM_NET_LOTS,
                    "minBars": MIN_BARS, "openSkipBars": OPEN_SKIP_BARS, "maxVwapDistancePct": MAX_VWAP_DISTANCE_PCT,
                },
            }

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

    def on_bar_completed(self, code: str, bar: dict[str, Any], *, persist: bool = True) -> list[dict[str, Any]]:
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
            self._bars_processed += 1
            self._last_bar_at = datetime.fromtimestamp(close_ts / 1000, TW_TZ).isoformat()
            for signal in signals:
                self._fired["bull" if signal["kind"] == KIND_BULL else "bear"] += 1
        if signals and persist:
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
        state.cum_buy += main_buy
        state.cum_sell += main_sell
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
        record: dict[str, Any] | None = None
        if self._trace is not None:
            record = {
                "time": _hhmm(close_ts), "close": close, "vwap": round(vwap, 2) if vwap else None, "volume": volume,
                "mainBuy": main_buy, "mainSell": main_sell, "cumNet": state.cum_net, "cumGross": state.cum_gross,
            }
            self._trace.append(record)
        if vwap is None or vwap <= 0:
            if record is not None:
                record["skip"] = "no_vwap"
            return []
        above = close > vwap
        if state.prev_above_vwap is not None:
            if above and not state.prev_above_vwap:
                state.bull_vwap_cross_ts = close_ts
                if record is not None:
                    record["vwapCross"] = "up"
            elif not above and state.prev_above_vwap:
                state.bear_vwap_cross_ts = close_ts
                if record is not None:
                    record["vwapCross"] = "down"
        state.prev_above_vwap = above

        # 「翻」一定要從另一邊翻過來：累計最近一次非零的正負跟現在相反才算零軸穿越。開盤後從 0 走出
        # 的第一個方向只是開盤偏向，不是翻（另一台工具 3532 09:01 從 0 翻正沒發、11:38 真的從負翻正才發）。
        # 累計反向再翻回來就可以再發：翻負時解除翻多的已發旗標，翻正時解除翻空的。
        del before_net
        sign_after = _sign(state.cum_net)
        if sign_after != 0 and sign_after != state.last_sign:
            if state.last_sign == 0:
                if record is not None:
                    record["firstSign"] = "bull" if sign_after > 0 else "bear"
            elif sign_after > 0:
                state.bull_zero_cross_ts = close_ts
                state.fired_bear = False
                if record is not None:
                    record["zeroCross"] = "bull"
            else:
                state.bear_zero_cross_ts = close_ts
                state.fired_bull = False
                if record is not None:
                    record["zeroCross"] = "bear"
            state.last_sign = sign_after

        # 三個比率先算好記進 trace（暖機中也要看得到），再做門檻判定。
        volume_ratio = self._volume_ratio(state, close_ts)
        distance_pct = (close / vwap - 1) * 100
        if record is not None:
            record.update({
                "netRatio": round(state.cum_net / state.cum_gross, 4) if state.cum_gross else None,
                "volumeRatio": round(volume_ratio, 2) if volume_ratio is not None else None,
                "distancePct": round(distance_pct, 2), "aboveVwap": above,
            })
        if state.bar_count == 1 and taipei_minute_of_day(int(bar["ts"])) <= 9 * 60:
            # 從開盤第一根就看到的股票（收盤後重播、或開盤前就訂閱到）：累計值是完整的，只避開
            # 開盤前 5 根；暖機 10 根只保護較晚才訂閱到、累計只從訂閱起算的情況。正式環境
            # 2026-09-22 的 3532 是 09:01 累計從 0 翻正、09:05 再站上 VWAP，暖機 10 根會把它整個吃掉。
            state.warmup_bars = OPEN_SKIP_BARS + 1  # 前 4 根（收盤 09:01～09:04）不判定，09:05 起才看
        if state.bar_count < state.warmup_bars or state.cum_gross < MIN_MAIN_GROSS_LOTS:
            if record is not None:
                record["skip"] = "warming_up" if state.bar_count < state.warmup_bars else "thin_main_force"
            return []
        if volume_ratio is None:
            if record is not None:
                record["skip"] = "no_daily_volume" if not state.avg_daily_volume else "no_today_volume"
            return []
        net_ratio = state.cum_net / state.cum_gross
        window_start = close_ts - SYNC_WINDOW_MS

        def synced(zero_ts: int | None, vwap_ts: int | None) -> bool:
            return zero_ts is not None and zero_ts >= window_start and vwap_ts is not None and vwap_ts >= window_start

        def blockers(side: str) -> list[str]:
            """同步條件（A＋B 在視窗內）成立後，C～D 濾網哪幾個沒過；空清單就是觸發。"""
            if side == "bull":
                checks = [
                    (state.cum_net > 0, "主力累計不在正值"),
                    (state.cum_net >= MIN_CUM_NET_LOTS, f"主力累計 {state.cum_net:+d} 張未達 +{MIN_CUM_NET_LOTS} 張"),
                    (state.cum_sell > 0, "主力只有買方量、沒有賣方，不算翻多"),
                    (above, "收盤在VWAP之下"),
                    (net_ratio >= NET_RATIO_MIN, f"主力淨額率 {net_ratio * 100:+.1f}% 未達 +{NET_RATIO_MIN * 100:.0f}%"),
                    (volume_ratio >= VOLUME_RATIO_MIN, f"量比 {volume_ratio:.2f}× 未達 {VOLUME_RATIO_MIN:g}×"),
                    (0 <= distance_pct <= MAX_VWAP_DISTANCE_PCT, f"距VWAP {distance_pct:+.2f}% 不在 0～+{MAX_VWAP_DISTANCE_PCT:g}%"),
                ]
            else:
                checks = [
                    (state.cum_net < 0, "主力累計不在負值"),
                    (state.cum_net <= -MIN_CUM_NET_LOTS, f"主力累計 {state.cum_net:+d} 張未達 -{MIN_CUM_NET_LOTS} 張"),
                    (state.cum_buy > 0, "主力只有賣方量、沒有買方，不算翻空"),
                    (not above, "收盤在VWAP之上"),
                    (net_ratio <= -NET_RATIO_MIN, f"主力淨額率 {net_ratio * 100:+.1f}% 未達 -{NET_RATIO_MIN * 100:.0f}%"),
                    (volume_ratio >= VOLUME_RATIO_MIN, f"量比 {volume_ratio:.2f}× 未達 {VOLUME_RATIO_MIN:g}×"),
                    (-MAX_VWAP_DISTANCE_PCT <= distance_pct <= 0, f"距VWAP {distance_pct:+.2f}% 不在 -{MAX_VWAP_DISTANCE_PCT:g}～0%"),
                ]
            return [why for ok, why in checks if not ok]

        group_name, name = _group_and_name(code)
        common = {
            "tradeDate": trade_date, "ticker": code, "name": name, "groupName": group_name,
            "barTs": close_ts, "price": close,
        }
        signals: list[dict[str, Any]] = []

        if not state.fired_bull and synced(state.bull_zero_cross_ts, state.bull_vwap_cross_ts):
            failed = blockers("bull")
            if record is not None:
                record["bull"] = {"synced": True, "blockers": failed}
            if not failed:
                strong = net_ratio >= NET_RATIO_STRONG and volume_ratio >= VOLUME_RATIO_STRONG
                state.fired_bull = True
                signals.append({
                    **common, "kind": KIND_BULL,
                    "label": "主力累計強勢翻多" if strong else "主力累計翻多",
                    "note": _note(state.bull_zero_cross_ts, state.bull_vwap_cross_ts, net_ratio, distance_pct, volume_ratio, state.cum_net),
                })

        if not state.fired_bear and synced(state.bear_zero_cross_ts, state.bear_vwap_cross_ts):
            failed = blockers("bear")
            if record is not None:
                record["bear"] = {"synced": True, "blockers": failed}
            if not failed:
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


def _merged_day_bars(
    code: str,
    trade_date: str,
    history: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """把當天的 1 分 K 價量（kbars 或備援來源）跟已落盤的主力副圖列依時間合起來，給重播用。"""
    day_bars = sorted(
        (bar for bar in history.get("bars", []) if taipei_trade_date(int(bar["ts"])) == trade_date),
        key=lambda bar: int(bar["ts"]),
    )
    if not day_bars:
        return [], {}, []
    main_rows = {int(row["ts"]): row for row in load_main_force_bars(code, "1m", trade_date=trade_date)}
    merged = []
    for bar in day_bars:
        row = main_rows.get(int(bar["ts"]))
        merged.append({
            "ts": int(bar["ts"]), "close": bar["close"], "volume": bar.get("volume", 0),
            "main_buy_volume": row["main_buy_volume"] if row else 0,
            "main_sell_volume": row["main_sell_volume"] if row else 0,
            "total_amount": row.get("total_amount", 0) if row else 0,
        })
    return day_bars, main_rows, merged


def inspect_flip_signals(
    code: str,
    trade_date: str,
    *,
    service: Any = None,
    hub: Any = None,
    include_trace: bool = False,
) -> dict[str, Any]:
    """單檔重播當天的翻多空判定過程：每個零軸／VWAP 穿越的時間、同步視窗內被哪個濾網擋下
    （nearMisses），用來跟另一台工具對條件。不寫入訊號、不動即時偵測器的狀態。"""
    code = str(code).strip().upper()
    history = get_stock_history_bars_1m(code, calendar_days=5, service=service, hub=hub)
    day_bars, main_rows, merged = _merged_day_bars(code, trade_date, history)
    monitor = MainForceFlipMonitor()
    monitor.enable_trace()
    signals: list[dict[str, Any]] = []
    for bar in merged:
        signals.extend(monitor.on_bar_completed(code, bar, persist=False))
    state = monitor._states.get(code)
    trace = monitor.trace()
    near_misses = [row for row in trace if (row.get("bull") or {}).get("blockers") or (row.get("bear") or {}).get("blockers")]
    vwap = monitor._vwap(state) if state else None
    compact_keys = ("time", "close", "vwap", "cumNet", "cumGross", "netRatio", "volumeRatio", "distancePct", "aboveVwap",
                    "skip", "firstSign", "zeroCross", "vwapCross")

    def compact(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row[key] for key in compact_keys if row.get(key) is not None}
    result: dict[str, Any] = {
        "code": code, "tradeDate": trade_date,
        "history": {"source": history.get("history_source"), "error": history.get("error"), "dayBars": len(day_bars)},
        "mainForce": {"rows": len(main_rows), "matchedBars": sum(1 for bar in day_bars if int(bar["ts"]) in main_rows)},
        "avgDailyVolume": state.avg_daily_volume if state else None,
        "totals": {
            "cumNet": state.cum_net, "cumGross": state.cum_gross, "volume": state.cum_volume,
            "vwap": round(vwap, 2) if vwap else None,
        } if state else None,
        # 開盤後累計從 0 走出的第一個方向（不算翻）；真正從另一邊翻過來的才列在 zeroCrosses。
        "firstSign": next(({"time": row["time"], "dir": row["firstSign"]} for row in trace if row.get("firstSign")), None),
        "zeroCrosses": [{"time": row["time"], "dir": row["zeroCross"]} for row in trace if row.get("zeroCross")],
        "vwapCrosses": [{"time": row["time"], "dir": row["vwapCross"]} for row in trace if row.get("vwapCross")],
        "signals": signals,
        "nearMissCount": len(near_misses), "nearMisses": near_misses[:60],
        # 開盤前 15 根與每個穿越當下的狀態：另一台工具若在暖機期就發訊號，從這裡對得出來。
        "head": [compact(row) for row in trace[:15]],
        "crossStates": [compact(row) for row in trace if row.get("firstSign") or row.get("zeroCross") or row.get("vwapCross")][:40],
        "thresholds": monitor.status()["thresholds"],
    }
    if include_trace:
        result["trace"] = trace
    return result


def backfill_flip_signals(
    trade_date: str,
    *,
    service: Any = None,
    hub: Any = None,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    """重播 trade_date 當天、補回偵測器不在線時漏掉的翻多空訊號。

    每分鐘價量用 Shioaji kbars（一檔一天約 30KB）；主力買賣張數與當日累計成交額
    用當天主力副圖收集器已經落盤的 main_force_bars（不花額度）。只重播當天有主力
    副圖資料的股票（＝當天被訂閱到、有 tick 的）。重播前先刪掉這檔當天既有的翻多空
    紀錄，以完整 09:00 起的當日 kbars 重算為準（即時路徑訂閱較晚時累計值只從訂閱
    起算，不準）；只有真的拿到當天 kbars 才刪除重寫。歷史額度用完時整批停下、回報
    quotaBlocked，排程之後再試。"""
    target = codes if codes is not None else list_main_force_codes_for_date(trade_date, "1m")
    group_codes = {str(code).upper() for members in STOCK_GROUPS.values() for code, _name in members}
    target_codes = sorted(code for code in {str(code).strip().upper() for code in target} if code in group_codes)
    monitor = get_main_force_flip_monitor()
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
            day_bars, _main_rows, merged = _merged_day_bars(code, trade_date, history)
            if not day_bars:
                skipped_no_bars += 1
                if error:
                    failures.append({"code": code, "error": str(error)})
                continue
            delete_signals_for_ticker(trade_date, code, FLIP_SIGNAL_KINDS)
            monitor.reset_for_backfill(code, trade_date)
            for bar in merged:
                signals_emitted += len(monitor.on_bar_completed(code, bar))
            bars_replayed += len(merged)
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


def flip_signal_backfill_status() -> dict[str, Any]:
    with _backfill_status_lock:
        return dict(_backfill_status)


def start_flip_signal_backfill(trade_date: str | None = None) -> dict[str, Any]:
    """背景執行緒觸發一次重播回補；已經在跑就不重複啟動。trade_date 預設今天。"""
    trade_date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    with _backfill_status_lock:
        if _backfill_status["running"]:
            return {"started": False, "reason": "already_running", "tradeDate": _backfill_status["tradeDate"]}
        _backfill_status.update({"running": True, "tradeDate": trade_date, "result": None})

    def _run() -> None:
        try:
            result = backfill_flip_signals(trade_date)
        except Exception as error:  # noqa: BLE001
            result = {"tradeDate": trade_date, "error": f"{type(error).__name__}: {error}"}
            logger.exception("主力累計翻多空回補整體失敗 trade_date=%s", trade_date)
        with _backfill_status_lock:
            _backfill_status.update({"running": False, "result": result})
        logger.info("主力累計翻多空回補完成: %s", result)

    threading.Thread(target=_run, name="hanstock-main-force-flip-backfill", daemon=True).start()
    return {"started": True, "tradeDate": trade_date}
