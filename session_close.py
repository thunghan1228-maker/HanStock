"""休市日（週末、國定假日）與交易日 08:45 前，全站改顯示上一個交易日收盤。

使用者 2026-10-04（週日）：TWSE 公開查詢 API 在週末跑測試盤時會回測試用的假價（鴻海 276 漲停、
好幾檔成交價 0），tw-groups 首頁跟盤中333／刀劍空／醞釀發動的即時判斷全部跟著亂掉。這裡告訴 worker
「現在該不該顯示上一個交易日收盤」（held），並附上族群成員（含股期標的清單）最新一個交易日的日K
收盤／漲跌／成交量，worker 在暫留時拿它取代 TWSE 即時報價。

交易日判斷用 trading_days（平日且不在休市日曆）；交易日 08:45 前也算暫留，跟大戶力排行同一條規則。
漲停／跌停旗標依證交所升降單位從前一日收盤推算（±10% 後往內靠到升降單位），是沒有官方漲跌停價
時的近似值。
"""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_connection, initialize_database
from stock_groups import STOCK_GROUPS
from trading_days import is_trading_day

TW_TZ = timezone(timedelta(hours=8))
HOLD_UNTIL_MINUTE = 8 * 60 + 45  # 下一個交易日開盤前 15 分鐘，跟 persistent_app.RANKING_HOLD_UNTIL_MINUTE 一致
CACHE_SECONDS = 300
LOOKBACK_CALENDAR_DAYS = 14
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "value": None}


def _now() -> datetime:
    return datetime.now(TW_TZ)


def should_hold_previous_close(now: datetime) -> bool:
    """非交易日整天、交易日 08:45 前：顯示上一個交易日收盤。"""
    if not is_trading_day(now):
        return True
    return now.hour * 60 + now.minute < HOLD_UNTIL_MINUTE


def tick_size(price: float) -> float:
    """證交所股票升降單位。"""
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def limit_prices(prev_close: float) -> tuple[float, float]:
    """前一日收盤推算當天的（漲停價, 跌停價）：±10% 後往內靠到升降單位（漲停無條件捨去、跌停無條件進位）。"""
    raw_up = prev_close * 1.1
    raw_down = prev_close * 0.9
    up_tick = tick_size(raw_up)
    down_tick = tick_size(raw_down)
    up = math.floor(raw_up / up_tick + 1e-9) * up_tick
    down = math.ceil(raw_down / down_tick - 1e-9) * down_tick
    return round(up, 2), round(down, 2)


def _load_recent_bars(codes: list[str], *, today: str) -> dict[str, dict[str, tuple[float, int]]]:
    """{代號: {日期: (收盤, 成交量張)}}，只取 today 之前、最近 LOOKBACK_CALENDAR_DAYS 天內的日K。"""
    initialize_database()
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    out: dict[str, dict[str, tuple[float, int]]] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"""
                SELECT stock_code, substr(bar_time, 1, 10) AS d, close, volume FROM bars_1d
                WHERE stock_code IN ({placeholders}) AND bar_time < ? AND bar_time >= ?
                """,
                (*batch, today, since),
            ).fetchall()
            for row in rows:
                close = float(row["close"])
                if close > 0:
                    out.setdefault(str(row["stock_code"]), {})[str(row["d"])] = (close, int(row["volume"] or 0))
    return out


def compute_session_close(now: datetime | None = None) -> dict[str, Any]:
    """回 {held, tradingDay, today, session, prevSession, stocks: {代號: {close, prevClose, change, pct, volume,
    limitUp, limitDown}}}；session＝today 之前最新一個有日K的交易日，prevSession＝再前一個（全市場日期，
    個股那兩天缺任何一天就沒有 pct／change，worker 會退回即時報價）。"""
    now = now or _now()
    today = now.strftime("%Y-%m-%d")
    codes = sorted({str(code).strip().upper() for members in STOCK_GROUPS.values() for code, _name in members})
    bars = _load_recent_bars(codes, today=today)
    dates = sorted({day for by_day in bars.values() for day in by_day}, reverse=True)
    session = dates[0] if dates else None
    prev_session = dates[1] if len(dates) > 1 else None
    stocks: dict[str, dict[str, Any]] = {}
    if session:
        for code, by_day in bars.items():
            bar = by_day.get(session)
            if not bar:
                continue
            close, volume = bar
            prev_bar = by_day.get(prev_session) if prev_session else None
            prev_close = prev_bar[0] if prev_bar else None
            entry: dict[str, Any] = {
                "close": close, "prevClose": prev_close, "change": None, "pct": None,
                "volume": volume, "limitUp": False, "limitDown": False,
            }
            if prev_close:
                up, down = limit_prices(prev_close)
                entry.update({
                    "change": round(close - prev_close, 2),
                    "pct": round((close / prev_close - 1) * 100, 2),
                    "limitUp": close >= up - 1e-6,
                    "limitDown": close <= down + 1e-6,
                })
            stocks[code] = entry
    return {
        "status": "ok",
        "today": today,
        "tradingDay": is_trading_day(now),
        "held": should_hold_previous_close(now),
        "session": session,
        "prevSession": prev_session,
        "count": len(stocks),
        "stocks": stocks,
        "generatedAt": now.isoformat(timespec="seconds"),
    }


def get_session_close() -> dict[str, Any]:
    """快取 5 分鐘：日K只在收盤後才會變，held 在 08:45 翻面時最多晚 5 分鐘。"""
    now = _now()
    key = f"{now.strftime('%Y-%m-%d')}:{should_hold_previous_close(now)}"
    mono = time.monotonic()
    with _cache_lock:
        if _cache["key"] == key and _cache["value"] is not None and mono - _cache["at"] < CACHE_SECONDS:
            return _cache["value"]
    value = compute_session_close(now)
    with _cache_lock:
        _cache.update({"key": key, "at": mono, "value": value})
    return value
