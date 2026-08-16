"""隔日 09:00～09:30 大單賣出達前日預估隔日賣壓 50% 的即時訊號。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from daytrade_flow_store import load_daytrade_rows
from intraday_signal_store import load_latest_signals_by_kind, save_intraday_signals
from otc_index import TW_TZ


SIGNAL_KIND = "daytradeEarlySell50"
SIGNAL_LABEL = "早盤大單賣出達前日預估隔日賣壓 50%"
THRESHOLD_RATE = 0.50
PREPARE_START_MINUTE = 8 * 60 + 50
WINDOW_START_MINUTE = 9 * 60
WINDOW_END_MINUTE = 9 * 60 + 30


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
