"""老師版「大戶%」：今日累積（同秒同方向併單 ≥7 張的大戶買 − 大戶賣，開盤第一筆算賣）÷ 近 5 日平均成交值 × 100。

2026-10-18 用使用者貼的 10/16 上午 18 檔、4 個時間點（39 筆）對過：平均差 0.7、八成在 1 以內；
分母不是當日成交值，所以越晚累積越大（華星光 09:20 -13.2 → 10:30 -31.0）。
K 棒的 big_buy_amount／big_sell_amount 由 market_data_hub（即時）與 stock_bar_bootstrap（歷史逐筆回補）算好，
這裡只把今天的 5 分 K 加起來；近 5 日平均成交值用日K估（(高+低+收)÷3 × 成交量），日K沒有成交金額欄位。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Iterable, Optional

from otc_index import TW_TZ, taipei_trade_date

AVG_DAYS = 5
MAX_CODES = 600
FIRST_BAR_MINUTE = 9 * 60
_avg_cache: dict[tuple[str, str], Optional[float]] = {}
_avg_lock = threading.Lock()


def avg_turnover(code: str, trade_date: str, *, loader=None) -> Optional[float]:
    """trade_date 之前（不含當天）最近 AVG_DAYS 個交易日的平均成交值（元）；日K不足就回 None。"""
    key = (code, trade_date)
    with _avg_lock:
        if key in _avg_cache:
            return _avg_cache[key]
    if loader is None:
        from daily_bars_store import load_daily_bars as loader
    bars = [bar for bar in loader(code, limit=AVG_DAYS + 3) if str(bar.get("ts", ""))[:10] < trade_date]
    bars = bars[-AVG_DAYS:]
    value: Optional[float] = None
    if len(bars) == AVG_DAYS:
        amounts = [
            (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3 * int(bar["volume"]) * 1000
            for bar in bars
        ]
        value = sum(amounts) / AVG_DAYS if sum(amounts) > 0 else None
    with _avg_lock:
        if len(_avg_cache) > 5000:
            _avg_cache.clear()
        _avg_cache[key] = value
    return value


def _bar_minute(ts_ms: int) -> int:
    local = datetime.fromtimestamp(ts_ms / 1000, TW_TZ)
    return local.hour * 60 + local.minute


def summarize(bars: Iterable[dict[str, Any]], avg_amount: Optional[float], *, interval_minutes: int = 5) -> dict[str, Any]:
    """今天的 5 分 K → 累積大戶淨額、大戶%，以及每根收完時的大戶%（回放用，時間標在 K 棒結束）。"""
    ordered = sorted((bar for bar in bars if isinstance(bar, dict) and "ts" in bar), key=lambda bar: int(bar["ts"]))
    usable = [bar for bar in ordered if "big_buy_amount" in bar]
    net = 0.0
    series: list[list[Any]] = []
    for bar in usable:
        net += float(bar.get("big_buy_amount") or 0) - float(bar.get("big_sell_amount") or 0)
        end = _bar_minute(int(bar["ts"])) + interval_minutes
        label = f"{end // 60:02d}:{end % 60:02d}"
        pct = round(net / avg_amount * 100, 1) if avg_amount else None
        series.append([label, pct])
    # 開盤那根沒有、或中間缺了沒有老師版欄位的 K 棒（例如這功能上線前的舊資料）→ 不完整，前端要標示
    complete = bool(usable) and len(usable) == len(ordered) and _bar_minute(int(usable[0]["ts"])) == FIRST_BAR_MINUTE
    # 今日最高／最低（今日功課「最高 ≥3%」開關用；只有即時 K 棒有 OHLC，資料庫存的主力 K 棒沒有，所以可能是 None）
    highs = [float(bar["high"]) for bar in ordered if bar.get("high")]
    lows = [float(bar["low"]) for bar in ordered if bar.get("low")]
    return {
        "high": max(highs) if highs else None,
        "low": min(lows) if lows else None,
        "net": round(net),
        "avgAmount": round(avg_amount) if avg_amount else None,
        "pct": round(net / avg_amount * 100, 1) if avg_amount and usable else None,
        "complete": complete,
        "series": series,
    }


def _today_bars(code: str, trade_date: str, hub: Any) -> list[dict[str, Any]]:
    from main_force_store import load_main_force_bars

    merged: dict[int, dict[str, Any]] = {}
    for bar in load_main_force_bars(code, "5m", trade_date=trade_date, limit=200):
        merged[int(bar["ts"])] = bar
    if hub is not None and trade_date == datetime.now(TW_TZ).strftime("%Y-%m-%d"):
        for bar in hub.get_live_bars(code) or []:
            ts = int(bar.get("ts", 0) or 0)
            if ts and taipei_trade_date(ts) == trade_date:
                merged[ts] = {**merged.get(ts, {}), **bar}
    return [merged[ts] for ts in sorted(merged)]


def payload(codes: Iterable[str], trade_date: Optional[str] = None, *, hub: Any = None) -> dict[str, Any]:
    normalized = list(dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip()))
    if not normalized:
        raise ValueError("codes 不可空白")
    if len(normalized) > MAX_CODES:
        raise ValueError(f"一次最多 {MAX_CODES} 檔")
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    date = trade_date or today
    started = time.monotonic()

    def build(day: str) -> dict[str, Any]:
        return {code: summarize(_today_bars(code, day, hub), avg_turnover(code, day)) for code in normalized}

    items = build(date)
    held_from = None
    if not trade_date and not any(item["series"] for item in items.values()):
        # 週末／開盤前今天還沒資料：沿用上一個有資料的交易日（跟盤中大戶力排行一樣）
        from main_force_store import latest_trade_date_with_data

        previous = latest_trade_date_with_data(today)
        if previous:
            held_from, date = today, previous
            items = build(date)
    return {
        "status": "ok",
        "tradeDate": date,
        "heldFrom": held_from,
        "formula": "今日累積（同秒同方向併單 ≥7 張大戶買 − 大戶賣；開盤第一筆算賣）÷ 近 5 日平均成交值 × 100",
        "avgDays": AVG_DAYS,
        "elapsedMs": round((time.monotonic() - started) * 1000),
        "items": items,
    }
