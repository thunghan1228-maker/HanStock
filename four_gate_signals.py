"""四項精選（目前簡化為2項）：主力方向確認 → 價格突破/跌破關鍵位置。

原本規格是四個條件同時成立才發訊號：
1. 前日預估賣壓金額 > 1 億元
2. 盤中即時金額達到前日預估賣壓的分時門檻
3. 主力多空淨額比：當分鐘 ≥+50%（強多）或 ≤-50%（強空），且前一分鐘同方向
4. 價格位置：強多＝現價>VWAP 且突破09:00-09:04首五分鐘高點；
   強空＝現價<VWAP 且跌破09:00-09:04首五分鐘低點

條件1/2需要每天全市場（約1900檔）歷史逐筆掃描才有「前日預估賣壓金額」，
這個掃描已在2026-09-10被永久停用（成本考量）。使用者決定拿掉條件1/2，
只保留條件3/4（同一股票、同一方向一天只發一次）。

候選股票不再需要額外掃描：直接用「今日已經有主力副圖資料」的股票（也就是
main_force_collector 目前實際在追蹤的股票），沒有新增任何 Shioaji 訂閱。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from intraday_signal_store import save_intraday_signals
from main_force_store import list_tracked_stock_codes, load_main_force_bars
from otc_index import TW_TZ
from stock_bar_bootstrap import get_resilient_stock_bars

WINDOW_START_MINUTE = 9 * 60
WINDOW_END_MINUTE = 13 * 60 + 30
BUY_KIND = "fourGateBuy"
SELL_KIND = "fourGateSell"
BUY_LABEL = "精選（強多）"
SELL_LABEL = "精選（強空）"


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


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _minute_net_ratios(
    bars_1m: list[dict[str, Any]],
    today: str,
    up_to_minute: int,
) -> tuple[float | None, float | None]:
    """回傳（當分鐘主力淨額比, 前一分鐘主力淨額）。"""
    minute_net: dict[int, float] = {}
    for bar in bars_1m:
        bar_time = _bar_datetime(bar.get("ts"))
        if bar_time is None or bar_time.strftime("%Y-%m-%d") != today:
            continue
        minute = _minute_of_day(bar_time)
        if minute < WINDOW_START_MINUTE or minute > min(up_to_minute, WINDOW_END_MINUTE):
            continue
        buy = max(0.0, float(bar.get("main_buy_amount") or 0))
        sell = max(0.0, float(bar.get("main_sell_amount") or 0))
        total = buy + sell
        minute_net[minute] = (buy - sell) / total if total > 0 else 0.0
    current_ratio = minute_net.get(up_to_minute)
    prior_minutes = [m for m in minute_net if m < up_to_minute]
    previous_ratio = minute_net[max(prior_minutes)] if prior_minutes else None
    return current_ratio, previous_ratio


def _price_position(bars_5m: list[dict[str, Any]], today: str) -> dict[str, Any] | None:
    """VWAP（今日累計，5分K）與 09:00-09:04 首五分鐘高低點。"""
    todays = [
        bar for bar in bars_5m
        if (bt := _bar_datetime(bar.get("ts"))) is not None and bt.strftime("%Y-%m-%d") == today
    ]
    if not todays:
        return None
    todays.sort(key=lambda bar: int(bar.get("ts") or 0))
    first_bar = todays[0]
    cum_pv = 0.0
    cum_v = 0.0
    for bar in todays:
        typical = (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3
        volume = float(bar.get("volume") or 0)
        cum_pv += typical * volume
        cum_v += volume
    vwap = cum_pv / cum_v if cum_v > 0 else float(todays[-1]["close"])
    return {
        "vwap": vwap,
        "firstHigh": float(first_bar["high"]),
        "firstLow": float(first_bar["low"]),
        "price": float(todays[-1]["close"]),
    }


def evaluate_ticker(service: Any, hub: Any, ticker: str, now: datetime) -> dict[str, Any] | None:
    """檢查單一股票的2項條件（主力淨額比、價格位置）；全部通過才回傳訊號。"""
    current = now.astimezone(TW_TZ)
    today = current.strftime("%Y-%m-%d")
    minute = _minute_of_day(current)
    if current.weekday() >= 5 or minute < WINDOW_START_MINUTE or minute > WINDOW_END_MINUTE:
        return None

    persisted = load_main_force_bars(ticker, "1m", trade_date=today, limit=1000)
    merged: dict[int, dict[str, Any]] = {}
    for source in (persisted, hub.get_live_bars_1m(ticker) or []):
        for bar in source:
            try:
                bar_ts = int(bar.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if bar_ts > 0:
                merged[bar_ts] = {**merged.get(bar_ts, {}), **bar}
    bars_1m = [merged[bar_ts] for bar_ts in sorted(merged)]
    current_ratio, previous_ratio = _minute_net_ratios(bars_1m, today, minute)
    if current_ratio is None or previous_ratio is None:
        return None

    bull_force_ok = current_ratio >= 0.50 and previous_ratio > 0
    bear_force_ok = current_ratio <= -0.50 and previous_ratio < 0
    if not bull_force_ok and not bear_force_ok:
        return None

    bars_5m = get_resilient_stock_bars(ticker, "5m", service=service, hub=hub).get("bars") or []
    position = _price_position(bars_5m, today)
    if position is None:
        return None
    bull_price_ok = position["price"] > position["vwap"] and position["price"] > position["firstHigh"]
    bear_price_ok = position["price"] < position["vwap"] and position["price"] < position["firstLow"]

    if bull_force_ok and bull_price_ok:
        kind, label, ref_value = BUY_KIND, BUY_LABEL, position["firstHigh"]
    elif bear_force_ok and bear_price_ok:
        kind, label, ref_value = SELL_KIND, SELL_LABEL, position["firstLow"]
    else:
        return None

    return {
        "tradeDate": today,
        "ticker": ticker,
        "name": ticker,
        "groupName": "精選",
        "kind": kind,
        "label": label,
        "barTs": int(current.timestamp() * 1000) // 60_000 * 60_000,
        "price": position["price"],
        "note": (
            f"主力淨額比 {current_ratio * 100:.0f}%｜VWAP {position['vwap']:.2f}"
            f"｜首5分{'高' if kind == BUY_KIND else '低'}點 {ref_value:.2f}"
        ),
    }


def collect_four_gate_signals(service: Any, hub: Any, now: datetime | None = None) -> dict[str, Any]:
    current = now.astimezone(TW_TZ) if now is not None else datetime.now(TW_TZ)
    today = current.strftime("%Y-%m-%d")
    candidates = list_tracked_stock_codes(today)
    pending: list[dict[str, Any]] = []
    errors: list[str] = []
    for ticker in candidates:
        try:
            signal = evaluate_ticker(service, hub, ticker, current)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ticker}: {exc}")
            continue
        if signal is not None:
            pending.append(signal)
    inserted = save_intraday_signals(pending)
    return {
        "tradeDate": today,
        "candidateCount": len(candidates),
        "inserted": inserted,
        "errors": errors[-20:],
    }
