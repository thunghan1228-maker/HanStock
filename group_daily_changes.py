"""族群最近幾個交易日的平均漲跌幅與名次（給盤中打 333 的 188／199 名單用）。

188 做多名單＝昨天跌最多的族群（今天可以買）、199 做空名單＝昨天漲最多的族群（今天可以空）；
另外「⚔️ 比昨天弱、🔪 還比前天更弱」也要昨天、前天的族群平均。資料來源是官方日K（bars_1d），
每檔算「當日收盤／前一個交易日收盤 −1」，族群取成員平均；交易日以全市場日K的日期為準，
某檔日K沒跟上（例如上櫃來源被擋）就不算進那天的平均，不會拿更早的日子冒充。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_connection, initialize_database
from stock_groups import STOCK_GROUPS

TW_TZ = timezone(timedelta(hours=8))
EXCLUDED_GROUPS = {"股期標的"}  # 不是產業族群，是股票期貨標的清單
CACHE_SECONDS = 1800
LOOKBACK_CALENDAR_DAYS = 30
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "value": None}


def _today() -> str:
    return datetime.now(TW_TZ).strftime("%Y-%m-%d")


def _load_rows(codes: list[str], *, today: str) -> list[tuple[str, str, float]]:
    """(代號, 交易日, 收盤) 列，只取 today 之前、最近 LOOKBACK_CALENDAR_DAYS 天內的。"""
    initialize_database()
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    out: list[tuple[str, str, float]] = []
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"""
                SELECT stock_code, substr(bar_time, 1, 10) AS d, close FROM bars_1d
                WHERE stock_code IN ({placeholders}) AND substr(bar_time, 1, 10) < ? AND substr(bar_time, 1, 10) >= ?
                """,
                (*batch, today, since),
            ).fetchall()
            out.extend((str(row["stock_code"]), str(row["d"]), float(row["close"])) for row in rows)
    return out


def compute_group_daily_changes(days: int = 3, *, today: str | None = None) -> dict[str, Any]:
    """回 {dates: [昨天, 前天, ...], groups: {族群: {pct: [...], rank: [...], members: [...]}},
    stocks: {代號: {pct: [...], close: [...], change: [...]}}}；pct 是百分比（+1.23 代表漲 1.23%），
    rank 1 = 那天最強；close／change 是那天的收盤價與對前一個交易日的漲跌金額（沒資料為 None）。"""
    today = today or _today()
    days = max(1, min(int(days), 10))
    groups = {name: members for name, members in STOCK_GROUPS.items() if name not in EXCLUDED_GROUPS}
    # 收盤價要涵蓋全部官方族群成員（含只在股期標的清單裡的，例如 2330）：前端三個大戶力分頁看
    # 昨天／前天時，盤中大戶力的平面排行也要有那天的成交價；族群平均仍只算 43 個一般族群。
    codes = sorted({str(code).strip().upper() for members in STOCK_GROUPS.values() for code, _name in members})
    closes: dict[str, dict[str, float]] = {}
    for code, day, close in _load_rows(codes, today=today):
        if close > 0:
            closes.setdefault(code, {})[day] = close
    market_dates = sorted({day for by_day in closes.values() for day in by_day}, reverse=True)[: days + 1]
    # 最舊的那天沒有再前一天可以比，不算一天
    dates = [day for index, day in enumerate(market_dates[:days]) if index + 1 < len(market_dates)]
    # 每檔個股同一組交易日的漲跌幅（馬火多：🐎 比昨天強、🚀 比前天強 要用），以及那天的收盤價與
    # 漲跌金額（三個大戶力分頁切到昨天／前天時當成交價、漲跌欄位用；使用者 2026-09-24）。
    per_stock: dict[str, dict[str, Any]] = {}
    for code, by_day in closes.items():
        pcts: list[float | None] = []
        close_list: list[float | None] = []
        change_list: list[float | None] = []
        for index, day in enumerate(dates):
            prev_day = market_dates[index + 1] if index + 1 < len(market_dates) else None
            close = by_day.get(day)
            prev_close = by_day.get(prev_day) if prev_day else None
            close_list.append(close)
            if close is not None and prev_close:
                pcts.append(round((close / prev_close - 1) * 100, 2))
                change_list.append(round(close - prev_close, 2))
            else:
                pcts.append(None)
                change_list.append(None)
        if any(value is not None for value in close_list):
            per_stock[code] = {"pct": pcts, "close": close_list, "change": change_list}
    per_group: dict[str, dict[str, Any]] = {}
    for name, members in groups.items():
        pcts: list[float | None] = []
        counts: list[int] = []
        for index, day in enumerate(dates):
            prev_day = market_dates[index + 1] if index + 1 < len(market_dates) else None
            values: list[float] = []
            if prev_day:
                for code, _stock_name in members:
                    by_day = closes.get(str(code).strip().upper(), {})
                    if day in by_day and prev_day in by_day:
                        values.append((by_day[day] / by_day[prev_day] - 1) * 100)
            pcts.append(round(sum(values) / len(values), 3) if values else None)
            counts.append(len(values))
        per_group[name] = {"pct": pcts, "members": counts, "rank": [None] * len(dates)}
    for index in range(len(dates)):
        ordered = sorted(
            (name for name, info in per_group.items() if info["pct"][index] is not None),
            key=lambda name: per_group[name]["pct"][index], reverse=True,
        )
        for position, name in enumerate(ordered, start=1):
            per_group[name]["rank"][index] = position
    return {
        "status": "ok", "today": today, "dates": dates, "groupCount": len(per_group),
        "rankedCount": [sum(1 for info in per_group.values() if info["rank"][i] is not None) for i in range(len(dates))],
        "groups": per_group,
        "stocks": per_stock,
        "generatedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"),
    }


def get_group_daily_changes(days: int = 3) -> dict[str, Any]:
    """快取半小時：日K只在收盤後才會變。"""
    key = f"{_today()}:{days}"
    now = time.monotonic()
    with _cache_lock:
        if _cache["key"] == key and _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
            return _cache["value"]
    value = compute_group_daily_changes(days)
    with _cache_lock:
        _cache.update({"key": key, "at": now, "value": value})
    return value
