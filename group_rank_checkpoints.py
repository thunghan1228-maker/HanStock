"""族群名次的半小時紀錄（2026-10-16 使用者：老師族群明細最下面有「今天23-3-11-24 🔽🔺🔽🔽」）。

老師每個族群明細最下面記了今天每半小時的族群名次，箭頭是跟上一個時間點比；馬火多(30) 只收「最近一次沒有往下掉」的
族群裡的 🐎🚀 股（光學鏡頭 10:30 名次從 11 掉到 24＝🔽，中揚光、今國光雖有符號也不進 30）。

這裡用資料庫 bars_5m 裡已存的每檔 5 分 K（intraday_kline_signals 每根走完就存，Railway 重開也不會掉）算：
時間點 T 的價格＝開始時間早於 T 的最後一根 5 分 K 收盤；漲跌幅對昨天收盤（官方日K）；族群名次＝族內平均漲跌幅由高到低。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

TW_TZ = timezone(timedelta(hours=8))
# 第一個點用開盤第一根 5 分 K（09:00～09:05）；之後每半小時一次
CHECKPOINTS = ("09:05", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30")
CACHE_SECONDS = 60

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "value": None}


def _groups() -> dict[str, list[str]]:
    from stock_groups import STOCK_GROUPS, UNRANKED_GROUP_NAMES

    return {
        name: [str(code).strip().upper() for code, _name in members]
        for name, members in STOCK_GROUPS.items() if name not in UNRANKED_GROUP_NAMES
    }


def _prev_closes(trade_date: str) -> dict[str, float]:
    """每檔昨天收盤：官方日K最新一天如果已經是今天（收盤後），就往前取一天。"""
    from group_daily_changes import get_group_daily_changes

    daily = get_group_daily_changes(3)
    dates = daily.get("dates") or []
    idx = 1 if dates and dates[0] == trade_date else 0
    out: dict[str, float] = {}
    for code, info in (daily.get("stocks") or {}).items():
        closes = info.get("close") or []
        if len(closes) > idx and closes[idx]:
            out[str(code).upper()] = float(closes[idx])
    return out


def _load_closes(trade_date: str, codes: list[str]) -> dict[str, list[tuple[str, float]]]:
    """{代號: [(bar 開始時間 HH:MM, 收盤), ...]}，時間由早到晚。"""
    from database import get_connection, initialize_database

    initialize_database()
    out: dict[str, list[tuple[str, float]]] = {}
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT stock_code, bar_time, close FROM bars_5m
               WHERE bar_time >= ? AND bar_time < ? || 'z'
               ORDER BY stock_code, bar_time""",
            (trade_date, trade_date),
        ).fetchall()
    wanted = set(codes)
    for row in rows:
        code = str(row["stock_code"]).upper()
        if code not in wanted:
            continue
        hm = str(row["bar_time"])[11:16]
        out.setdefault(code, []).append((hm, float(row["close"])))
    return out


def _bar_close_before(bars: list[tuple[str, float]], checkpoint: str) -> Optional[float]:
    """開始時間早於 checkpoint 的最後一根 5 分 K 收盤（09:05 → 09:00 那根）。"""
    price = None
    for hm, close in bars:
        if hm < checkpoint:
            price = close
        else:
            break
    return price


def compute_group_rank_checkpoints(
    trade_date: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    groups: Optional[dict[str, list[str]]] = None,
    prev_closes: Optional[dict[str, float]] = None,
    closes_loader: Optional[Callable[[str, list[str]], dict[str, list[tuple[str, float]]]]] = None,
) -> dict[str, Any]:
    now = now or datetime.now(TW_TZ)
    trade_date = trade_date or now.strftime("%Y-%m-%d")
    groups = groups if groups is not None else _groups()
    prev = prev_closes if prev_closes is not None else _prev_closes(trade_date)
    codes = sorted({code for members in groups.values() for code in members})
    bars = (closes_loader or _load_closes)(trade_date, codes)
    hm_now = now.strftime("%H:%M") if now.strftime("%Y-%m-%d") == trade_date else "23:59"
    times = [t for t in CHECKPOINTS if t <= hm_now]
    result: dict[str, dict[str, list[Any]]] = {name: {"ranks": [], "avgs": []} for name in groups}
    used_times: list[str] = []
    for t in times:
        avgs: dict[str, float] = {}
        for name, members in groups.items():
            pcts = []
            for code in members:
                base = prev.get(code)
                price = _bar_close_before(bars.get(code, []), t)
                if base and price:
                    pcts.append((price / base - 1) * 100)
            if pcts:
                avgs[name] = sum(pcts) / len(pcts)
        if not avgs:
            continue   # 這個時間點還沒有任何 5 分 K（例如資料庫沒存到）就略過
        used_times.append(t)
        ranked = sorted(avgs.items(), key=lambda item: -item[1])
        rank_of = {name: i + 1 for i, (name, _avg) in enumerate(ranked)}
        for name in groups:
            result[name]["ranks"].append(rank_of.get(name))
            result[name]["avgs"].append(round(avgs[name], 3) if name in avgs else None)
    return {
        "status": "ok", "date": trade_date, "times": used_times, "groupCount": len(groups),
        "groups": result, "generatedAt": now.isoformat(timespec="seconds"),
    }


def get_group_rank_checkpoints(trade_date: Optional[str] = None) -> dict[str, Any]:
    """快取 60 秒：5 分 K 每 5 分鐘才多一根。"""
    key = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    now_mono = time.monotonic()
    with _cache_lock:
        if _cache["key"] == key and _cache["value"] is not None and now_mono - _cache["at"] < CACHE_SECONDS:
            return _cache["value"]
    value = compute_group_rank_checkpoints(trade_date)
    with _cache_lock:
        _cache.update({"key": key, "at": now_mono, "value": value})
    return value
