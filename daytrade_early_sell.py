"""隔日 09:00～09:30 大單賣出達前日預估隔日賣壓 50% 的即時訊號。"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from daytrade_flow_store import load_daytrade_rows
from intraday_signal_store import load_latest_signals_by_kind, save_intraday_signals
from market_data_hub import _is_main_force_trade, _trade_side
from otc_index import TW_TZ, taipei_minute_of_day, taipei_trade_date
from stock_bar_bootstrap import (
    _fetch_historical_ticks,
    _field_values,
    _resolve_stock_contract,
    _shioaji_tick_to_ms,
)


SIGNAL_KIND = "daytradeEarlySell50"
SIGNAL_LABEL = "早盤大單賣出達前日預估隔日賣壓 50%"
THRESHOLD_RATE = 0.50
PREPARE_START_MINUTE = 8 * 60 + 50
WINDOW_START_MINUTE = 9 * 60
WINDOW_END_MINUTE = 9 * 60 + 30
_DEMO_CACHE_SECONDS = 6 * 60 * 60
_demo_cache_lock = threading.RLock()
_demo_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _taipei_now(now: datetime | None = None) -> datetime:
    return now.astimezone(TW_TZ) if now is not None else datetime.now(TW_TZ)


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _bar_datetime(timestamp: Any) -> datetime | None:
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        return None
    if not value:
        return None
    if value < 1_000_000_000_000:
        value *= 1000
    try:
        return datetime.fromtimestamp(value / 1000, TW_TZ)
    except (OSError, OverflowError, ValueError):
        return None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _estimated_next_day_sell_pressure(row: dict[str, Any]) -> float:
    """沿用戰鬥版隔日沖表格的「預估隔日賣壓」公式。"""
    buy = max(0.0, float(row.get("large_buy_amount") or 0))
    sell = max(0.0, float(row.get("large_sell_amount") or 0))
    turnover = max(0.0, float(row.get("total_turnover_amount") or 0))
    net = max(0.0, buy - sell)
    if net <= 0:
        return 0.0
    score = float(row.get("suspicion_score") or 0)
    if score <= 0:
        participation = buy / turnover * 100 if turnover > 0 else 0.0
        net_rate = net / turnover * 100 if turnover > 0 else 0.0
        late_buy = max(0.0, float(row.get("late_large_buy_amount") or 0))
        late_concentration = late_buy / buy * 100 if buy > 0 else 0.0
        price_impact = max(0.0, float(row.get("price_impact_pct") or 0))
        score = (
            _clamp(participation / 25, 0, 1) * 40
            + _clamp(net_rate / 15, 0, 1) * 25
            + _clamp(late_concentration / 80, 0, 1) * 20
            + _clamp(price_impact / 5, 0, 1) * 15
        )
    return net * _clamp((score - 20) / 80, 0.15, 0.85)


def _previous_saved_candidates(trade_date: str) -> tuple[str, list[dict[str, Any]]]:
    """找指定日期之前最近一個已有永久資料的交易日。"""
    cursor = date.fromisoformat(trade_date)
    for _ in range(14):
        cursor -= timedelta(days=1)
        if cursor.weekday() >= 5:
            continue
        rows = load_daytrade_rows(cursor.isoformat(), limit=2000)
        if rows:
            return cursor.isoformat(), rows
    return "", []


def historical_early_sell_signals_for_ticks(
    ticks: Any,
    row: dict[str, Any],
    trade_date: str,
) -> list[dict[str, Any]]:
    """用歷史逐筆成交重播早盤規則；只回傳示範，不寫入正式訊號。"""
    previous_pressure = _estimated_next_day_sell_pressure(row)
    if previous_pressure <= 0:
        return []

    timestamps = _field_values(ticks, "ts", "datetime")
    closes = _field_values(ticks, "close", "Close")
    volumes = _field_values(ticks, "volume", "Volume")
    tick_types = _field_values(ticks, "tick_type", "TickType")
    amounts = _field_values(ticks, "amount", "Amount")
    simtrades = _field_values(ticks, "simtrade", "Simtrade")
    size = min(len(timestamps), len(closes), len(volumes))
    five_minute_bars: dict[int, dict[str, float]] = {}

    for index in range(size):
        ts_ms = _shioaji_tick_to_ms(timestamps[index])
        if ts_ms is None or taipei_trade_date(ts_ms) != trade_date:
            continue
        minute = taipei_minute_of_day(ts_ms)
        if minute < WINDOW_START_MINUTE or minute > WINDOW_END_MINUTE:
            continue
        if index < len(simtrades) and bool(simtrades[index]):
            continue
        try:
            close = float(closes[index])
            volume = max(0, int(volumes[index] or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if close <= 0 or volume <= 0:
            continue
        try:
            amount = float(amounts[index] or 0) if index < len(amounts) else 0.0
        except (TypeError, ValueError, OverflowError):
            amount = 0.0
        if amount <= 0:
            amount = close * volume * 1000
        raw_tick_type = tick_types[index] if index < len(tick_types) else 0
        raw_tick_type = getattr(raw_tick_type, "value", raw_tick_type)
        try:
            tick_type = int(raw_tick_type)
        except (TypeError, ValueError, OverflowError):
            tick_type = 0
        trade = {"close": close, "volume": volume, "amount": amount, "tick_type": tick_type}
        if not _is_main_force_trade(trade) or _trade_side(trade) != "sell":
            continue
        bucket = ts_ms // 300_000 * 300_000
        bar = five_minute_bars.setdefault(bucket, {"sell": 0.0, "price": close})
        bar["sell"] += amount
        bar["price"] = close

    cumulative_sell = 0.0
    signals: list[dict[str, Any]] = []
    for bar_ts, bar in sorted(five_minute_bars.items()):
        cumulative_sell += bar["sell"]
        if cumulative_sell < previous_pressure * THRESHOLD_RATE:
            continue
        ratio = cumulative_sell / previous_pressure * 100
        signals.append({
            "tradeDate": trade_date,
            "ticker": str(row.get("ticker") or "").strip().upper(),
            "name": str(row.get("name") or row.get("ticker") or "").strip(),
            "groupName": "疑似隔日沖",
            "kind": SIGNAL_KIND,
            "label": SIGNAL_LABEL,
            "barTs": bar_ts,
            "price": max(0.01, float(bar["price"] or row.get("close_price") or 0.01)),
            "note": f"前日預估隔日賣壓 {previous_pressure:.0f}｜早盤大單賣出 {cumulative_sell:.0f}｜比例 {ratio:.1f}%",
            "demo": True,
        })
    return signals


def historical_early_sell_demo_snapshot(
    service: Any,
    trade_date: str,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """重播指定交易日 09:00～09:30，供前端顯示歷史警示示範。"""
    cached = _demo_cache.get(trade_date)
    if cached and time.monotonic() - cached[0] < _DEMO_CACHE_SECONDS:
        return {**cached[1], "signals": list(cached[1]["signals"])[:limit]}

    previous_date, candidates = _previous_saved_candidates(trade_date)
    if not candidates:
        return {"tradeDate": trade_date, "previousTradeDate": previous_date, "signals": [], "errors": ["找不到前一交易日候選資料"]}
    api = getattr(service, "api", None)
    logged_in = bool(getattr(getattr(service, "state", None), "logged_in", False))
    if api is None or not logged_in:
        raise RuntimeError("Shioaji 尚未登入，無法重播歷史警示")

    signals: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in candidates:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        try:
            contract = _resolve_stock_contract(service, ticker)
            if contract is None:
                continue
            ticks = _fetch_historical_ticks(api, contract, trade_date)
            signals.extend(historical_early_sell_signals_for_ticks(ticks, row, trade_date))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ticker}: {exc}")

    signals.sort(key=lambda item: (int(item["barTs"]), str(item["ticker"])))
    result = {
        "tradeDate": trade_date,
        "previousTradeDate": previous_date,
        "thresholdPct": THRESHOLD_RATE * 100,
        "window": "09:00～09:30（含 09:30）",
        "candidateCount": len(candidates),
        "signalCount": len(signals),
        "signals": signals,
        "errors": errors[-20:],
        "demo": True,
    }
    with _demo_cache_lock:
        _demo_cache[trade_date] = (time.monotonic(), result)
    return {**result, "signals": list(signals)[:limit]}


def monitored_candidates() -> tuple[str, list[dict[str, Any]]]:
    """讀取最近有效交易日名單；休市日不會改用空日期覆蓋。"""
    rows = load_daytrade_rows(None, limit=2000)
    if not rows:
        return "", []
    previous_date = str(rows[0].get("trade_date") or "")
    candidates: list[dict[str, Any]] = []
    for row in rows:
        estimated_sell_pressure = _estimated_next_day_sell_pressure(row)
        if estimated_sell_pressure <= 0:
            continue
        candidates.append({**row, "previous_estimated_sell_pressure": estimated_sell_pressure})
    return previous_date, candidates


def prepare_subscriptions(service: Any, now: datetime | None = None) -> dict[str, Any]:
    current = _taipei_now(now)
    minute = _minute_of_day(current)
    previous_date, candidates = monitored_candidates()
    if current.weekday() >= 5 or not (PREPARE_START_MINUTE <= minute <= WINDOW_END_MINUTE):
        return {"prepared": False, "previousTradeDate": previous_date, "monitoredCount": len(candidates)}
    codes = [str(row.get("ticker") or "").strip().upper() for row in candidates]
    codes = [code for code in dict.fromkeys(codes) if code]
    result = service.ensure_stock_subscriptions(codes) if codes else {"active_count": 0, "failed": {}}
    return {
        "prepared": bool(codes),
        "previousTradeDate": previous_date,
        "monitoredCount": len(codes),
        "activeCount": int(result.get("active_count") or 0),
        "failedCount": len(result.get("failed") or {}),
    }


def collect_early_sell_signals(
    service: Any,
    hub: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _taipei_now(now)
    today = current.strftime("%Y-%m-%d")
    minute = _minute_of_day(current)
    prepared = prepare_subscriptions(service, current)
    previous_date, candidates = monitored_candidates()
    in_window = current.weekday() < 5 and WINDOW_START_MINUTE <= minute <= WINDOW_END_MINUTE
    if not in_window:
        return {
            **prepared,
            "tradeDate": today,
            "previousTradeDate": previous_date,
            "inWindow": False,
            "inserted": [],
        }

    pending: list[dict[str, Any]] = []
    for row in candidates:
        ticker = str(row.get("ticker") or "").strip().upper()
        bars = hub.get_live_bars_1m(ticker)
        eligible: list[tuple[dict[str, Any], datetime]] = []
        for bar in bars:
            bar_time = _bar_datetime(bar.get("ts"))
            if bar_time is None or bar_time.strftime("%Y-%m-%d") != today:
                continue
            bar_minute = _minute_of_day(bar_time)
            if WINDOW_START_MINUTE <= bar_minute <= WINDOW_END_MINUTE:
                eligible.append((bar, bar_time))
        if not eligible:
            continue
        cumulative_sell = sum(max(0.0, float(bar.get("main_sell_amount") or 0)) for bar, _ in eligible)
        previous_pressure = float(row["previous_estimated_sell_pressure"])
        if cumulative_sell < previous_pressure * THRESHOLD_RATE:
            continue
        sell_bars = [(bar, bar_time) for bar, bar_time in eligible if float(bar.get("main_sell_amount") or 0) > 0]
        if not sell_bars:
            continue
        latest_bar, latest_time = sell_bars[-1]
        five_minute_ts = int(latest_time.timestamp() * 1000) // 300_000 * 300_000
        ratio = cumulative_sell / previous_pressure * 100
        pending.append({
            "tradeDate": today,
            "ticker": ticker,
            "name": str(row.get("name") or ticker),
            "groupName": "疑似隔日沖",
            "kind": SIGNAL_KIND,
            "label": SIGNAL_LABEL,
            "barTs": five_minute_ts,
            "price": max(0.01, float(latest_bar.get("close") or row.get("close_price") or 0.01)),
            "note": f"前日預估隔日賣壓 {previous_pressure:.0f}｜早盤大單賣出 {cumulative_sell:.0f}｜比例 {ratio:.1f}%",
        })

    inserted = save_intraday_signals(pending)
    return {
        **prepared,
        "tradeDate": today,
        "previousTradeDate": previous_date,
        "inWindow": True,
        "inserted": inserted,
    }


def early_sell_signal_snapshot(now: datetime | None = None, limit: int = 100) -> dict[str, Any]:
    current = _taipei_now(now)
    today = current.strftime("%Y-%m-%d")
    previous_date, candidates = monitored_candidates()
    return {
        "tradeDate": today,
        "previousTradeDate": previous_date,
        "thresholdPct": THRESHOLD_RATE * 100,
        "window": "09:00～09:30",
        "monitoredCount": len(candidates),
        "signals": load_latest_signals_by_kind(today, SIGNAL_KIND, limit=limit),
    }
