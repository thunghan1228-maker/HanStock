"""四項精選：賣壓夠大 → 盤中金額達標 → 主力方向確認 → 價格突破/跌破關鍵位置。

四個條件同時成立才發訊號（同一股票、同一方向一天只發一次）：
1. 前日預估賣壓金額 > 1 億元。
2. 盤中即時大單金額累計達到前日預估賣壓的分時門檻：
   09:00-09:29 ≥50%／09:30-09:59 ≥70%／10:00-10:59 ≥90%／11:00之後 ≥120%。
3. 主力多空淨額比：當分鐘 ≥+50%（強多）或 ≤-50%（強空），且前一分鐘同方向（>0 或 <0）。
4. 價格位置：強多＝現價>VWAP 且突破 09:00-09:04 首五分鐘高點；
   強空＝現價<VWAP 且跌破 09:00-09:04 首五分鐘低點。

條件 1/2 沿用 daytrade_early_sell.py 的「前日預估隔日賣壓」估計與逐分鐘累計金額；
條件 3/4 是本次新增，直接用既有的主力副圖（main_force_store）與 5 分 K 資料計算，
沒有新增任何 Shioaji 訂閱或收集器。

注意：monitored_candidates() 需要 daytrade_flow 每日全市場歷史掃描才有候選名單，
而該掃描已在 2026-09-10 被刻意永久停用（daytrade_flow_collector.py 的說法是
「約1900檔的歷史掃描」，成本考量）。在確認要用什麼股票範圍重新啟用每日掃描之前，
這裡會持續回傳空的候選清單——不是壞掉，是還沒有輸入資料。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from daytrade_early_sell import monitored_candidates
from intraday_signal_store import save_intraday_signals
from main_force_store import load_main_force_bars
from otc_index import TW_TZ
from stock_bar_bootstrap import get_resilient_stock_bars

MIN_PREVIOUS_PRESSURE = 100_000_000.0  # 條件1：前日預估賣壓金額 > 1億元
WINDOW_START_MINUTE = 9 * 60
WINDOW_END_MINUTE = 13 * 60 + 30
BUY_KIND = "fourGateBuy"
SELL_KIND = "fourGateSell"
BUY_LABEL = "四項精選（強多）"
SELL_LABEL = "四項精選（強空）"


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


def _threshold_rate(minute_of_day: int) -> float:
    """條件2：盤中金額達到前日預估賣壓的分時門檻。"""
    if minute_of_day < 9 * 60 + 30:
        return 0.50
    if minute_of_day < 10 * 60:
        return 0.70
    if minute_of_day < 11 * 60:
        return 0.90
    return 1.20


def _cumulative_amounts(
    bars_1m: list[dict[str, Any]],
    today: str,
    up_to_minute: int,
) -> tuple[float, float, float | None, float | None]:
    """回傳（累計買金額、累計賣金額、當分鐘主力淨額比、前一分鐘主力淨額）。"""
    minute_net: dict[int, float] = {}
    cum_buy = 0.0
    cum_sell = 0.0
    for bar in bars_1m:
        bar_time = _bar_datetime(bar.get("ts"))
        if bar_time is None or bar_time.strftime("%Y-%m-%d") != today:
            continue
        minute = _minute_of_day(bar_time)
        if minute < WINDOW_START_MINUTE or minute > min(up_to_minute, WINDOW_END_MINUTE):
            continue
        buy = max(0.0, float(bar.get("main_buy_amount") or 0))
        sell = max(0.0, float(bar.get("main_sell_amount") or 0))
        cum_buy += buy
        cum_sell += sell
        total = buy + sell
        minute_net[minute] = (buy - sell) / total if total > 0 else 0.0
    current_ratio = minute_net.get(up_to_minute)
    prior_minutes = [m for m in minute_net if m < up_to_minute]
    previous_ratio = minute_net[max(prior_minutes)] if prior_minutes else None
    return cum_buy, cum_sell, current_ratio, previous_ratio


def _price_position(bars_5m: list[dict[str, Any]], today: str) -> dict[str, Any] | None:
    """條件4：VWAP（今日累計，5分K）與 09:00-09:04 首五分鐘高低點。"""
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


def evaluate_ticker(service: Any, hub: Any, row: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """對單一候選股檢查四項條件；全部通過才回傳訊號字典，否則回傳 None。"""
    ticker = str(row.get("ticker") or "").strip().upper()
    previous_pressure = float(row.get("previous_estimated_sell_pressure") or 0)
    if previous_pressure < MIN_PREVIOUS_PRESSURE:  # 條件1
        return None

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
    cum_buy, cum_sell, current_ratio, previous_ratio = _cumulative_amounts(bars_1m, today, minute)
    if current_ratio is None or previous_ratio is None:
        return None

    threshold = previous_pressure * _threshold_rate(minute)  # 條件2
    bull_amount_ok = cum_buy >= threshold
    bear_amount_ok = cum_sell >= threshold
    bull_force_ok = current_ratio >= 0.50 and previous_ratio > 0  # 條件3
    bear_force_ok = current_ratio <= -0.50 and previous_ratio < 0

    bars_5m = get_resilient_stock_bars(ticker, "5m", service=service, hub=hub).get("bars") or []
    position = _price_position(bars_5m, today)
    if position is None:
        return None
    bull_price_ok = position["price"] > position["vwap"] and position["price"] > position["firstHigh"]  # 條件4
    bear_price_ok = position["price"] < position["vwap"] and position["price"] < position["firstLow"]

    if bull_amount_ok and bull_force_ok and bull_price_ok:
        kind, label, cumulative, ref_value = BUY_KIND, BUY_LABEL, cum_buy, position["firstHigh"]
    elif bear_amount_ok and bear_force_ok and bear_price_ok:
        kind, label, cumulative, ref_value = SELL_KIND, SELL_LABEL, cum_sell, position["firstLow"]
    else:
        return None

    return {
        "tradeDate": today,
        "ticker": ticker,
        "name": str(row.get("name") or ticker),
        "groupName": "四項精選",
        "kind": kind,
        "label": label,
        "barTs": int(current.timestamp() * 1000) // 60_000 * 60_000,
        "price": position["price"],
        "note": (
            f"前日預估賣壓 {previous_pressure:,.0f}｜盤中累計 {cumulative:,.0f}"
            f"（門檻 {threshold:,.0f}）｜主力淨額比 {current_ratio * 100:.0f}%"
            f"｜VWAP {position['vwap']:.2f}｜首5分{'高' if kind == BUY_KIND else '低'}點 {ref_value:.2f}"
        ),
    }


def collect_four_gate_signals(service: Any, hub: Any, now: datetime | None = None) -> dict[str, Any]:
    current = now.astimezone(TW_TZ) if now is not None else datetime.now(TW_TZ)
    _, candidates = monitored_candidates()
    pending: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in candidates:
        try:
            signal = evaluate_ticker(service, hub, row, current)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{row.get('ticker')}: {exc}")
            continue
        if signal is not None:
            pending.append(signal)
    inserted = save_intraday_signals(pending)
    return {
        "tradeDate": current.strftime("%Y-%m-%d"),
        "candidateCount": len(candidates),
        "inserted": inserted,
        "errors": errors[-20:],
    }
