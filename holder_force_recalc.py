"""用歷史逐筆成交重算「大戶%」，對照別家看盤工具的算法。

我們的盤中大戶力（market_data_hub）是「單筆成交 ≥ MAIN_FORCE_MIN_LOTS 張」才算大戶單；
2026-10-18 使用者貼的對照工具說明寫的是「大戶買賣力累積%（同秒併單 ≥7 張）」：
同一秒、同方向（外盤買／內盤賣）的成交先併成一筆，滿 7 張才算大戶。

這支只做對照：指定股票與交易日，從 Shioaji api.ticks() 抓整日逐筆，
依幾種門檻算到指定時間點為止的累積大戶%，回傳給人工比對，不寫進任何儲存。
大戶% = (大戶外盤買金額 − 大戶內盤賣金額) ÷ 累積成交金額 × 100。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any, Iterable, Optional

from market_data_hub import _trade_side
from otc_index import TW_TZ, taipei_trade_date
from stock_bar_bootstrap import (
    _fetch_historical_ticks,
    _field_values,
    _resolve_stock_contract,
    _shioaji_tick_to_ms,
)

MAX_CODES = 20
DEFAULT_CUTOFFS = ("09:20", "09:30", "13:30")
# t = 單筆成交張數；s = 同秒同方向併單後的張數。
VARIANT_MODES = ("t", "s")
VARIANT_LOTS = (5, 7, 10, 20)
SESSION_START_SEC = 9 * 3600
SESSION_END_SEC = 13 * 3600 + 30 * 60

_CACHE_MAX = 60
_TODAY_TTL_SECONDS = 120
_cache: "OrderedDict[tuple[str, str], tuple[float, list[tuple[int, int, int, float]]]]" = OrderedDict()
_cache_lock = threading.Lock()
_query_lock = threading.Lock()


def parse_cutoff(value: str) -> int:
    """'09:20' 或 '09:20:30' → 當日第幾秒（台灣時間）。"""
    try:
        parts = [int(part) for part in str(value).strip().split(":")]
    except ValueError as exc:
        raise ValueError(f"時間格式要 HH:MM：{value}") from exc
    if len(parts) == 2:
        parts.append(0)
    if len(parts) != 3:
        raise ValueError(f"時間格式要 HH:MM：{value}")
    hour, minute, second = parts
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        raise ValueError(f"時間格式要 HH:MM：{value}")
    return hour * 3600 + minute * 60 + second


def _sec_label(sec: int) -> str:
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def normalize_ticks(ticks: Any, *, trade_date: str) -> list[tuple[int, int, int, float]]:
    """逐筆 → [(當日秒數, 方向 1 買／-1 賣／0 中性, 張數, 金額)]，只留當日一般盤 09:00~13:30。"""
    timestamps = _field_values(ticks, "ts", "datetime")
    closes = _field_values(ticks, "close", "Close")
    volumes = _field_values(ticks, "volume", "Volume")
    tick_types = _field_values(ticks, "tick_type", "TickType")
    amounts = _field_values(ticks, "amount", "Amount")
    simtrades = _field_values(ticks, "simtrade", "Simtrade")
    rows: list[tuple[int, int, int, float]] = []
    for index in range(min(len(timestamps), len(closes), len(volumes))):
        if index < len(simtrades) and bool(simtrades[index]):
            continue
        ts_ms = _shioaji_tick_to_ms(timestamps[index])
        if ts_ms is None or taipei_trade_date(ts_ms) != trade_date:
            continue
        local = datetime.fromtimestamp(ts_ms / 1000, TW_TZ)
        sec = local.hour * 3600 + local.minute * 60 + local.second
        if sec < SESSION_START_SEC or sec > SESSION_END_SEC:
            continue
        try:
            close = float(closes[index])
            volume = max(0, int(volumes[index] or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if close <= 0 or volume <= 0:
            continue
        try:
            amount = float((amounts[index] if index < len(amounts) else 0) or 0)
        except (TypeError, ValueError, OverflowError):
            amount = 0.0
        if amount <= 0:
            amount = close * volume * 1000
        raw_type = tick_types[index] if index < len(tick_types) else 0
        raw_type = getattr(raw_type, "value", raw_type)
        try:
            side_name = _trade_side({"tick_type": int(raw_type)})
        except (TypeError, ValueError, OverflowError):
            side_name = "neutral"
        side = 1 if side_name == "buy" else -1 if side_name == "sell" else 0
        rows.append((sec, side, volume, amount))
    rows.sort(key=lambda row: row[0])
    return rows


def merge_same_second(rows: Iterable[tuple[int, int, int, float]]) -> list[tuple[int, int, int, float, int]]:
    """同一秒、同方向的成交併成一筆：[(秒, 方向, 張數, 金額, 併了幾筆)]。"""
    merged: "OrderedDict[tuple[int, int], list[float]]" = OrderedDict()
    for sec, side, volume, amount in rows:
        slot = merged.setdefault((sec, side), [0, 0.0, 0])
        slot[0] += volume
        slot[1] += amount
        slot[2] += 1
    return [(sec, side, int(v), float(a), int(n)) for (sec, side), (v, a, n) in merged.items()]


def _variant(units: Iterable[tuple[int, int, float]], total_amount: float, lots: int) -> dict[str, Any]:
    buy = sell = 0.0
    for side, volume, amount in units:
        if volume < lots:
            continue
        if side > 0:
            buy += amount
        elif side < 0:
            sell += amount
    gross = buy + sell
    return {
        "buy": round(buy),
        "sell": round(sell),
        "pct": round((buy - sell) / total_amount * 100, 2) if total_amount > 0 else None,
        "grossPct": round((buy - sell) / gross * 100, 2) if gross > 0 else None,
    }


def summarize_until(rows: list[tuple[int, int, int, float]], cutoff_sec: int) -> dict[str, Any]:
    """算到 cutoff（含該秒）為止的累積成交與各門檻大戶%。"""
    upto = [row for row in rows if row[0] <= cutoff_sec]
    total_amount = sum(row[3] for row in upto)
    merged = merge_same_second(upto)
    variants: dict[str, Any] = {}
    for lots in VARIANT_LOTS:
        variants[f"t{lots}"] = _variant(((s, v, a) for _, s, v, a in upto), total_amount, lots)
        variants[f"s{lots}"] = _variant(((s, v, a) for _, s, v, a, _n in merged), total_amount, lots)
    return {
        "ticks": len(upto),
        "totalAmount": round(total_amount),
        "variants": variants,
    }


def summarize_code(
    rows: list[tuple[int, int, int, float]],
    cutoffs: Iterable[int],
    *,
    raw: bool = False,
) -> dict[str, Any]:
    first = rows[0] if rows else None
    data: dict[str, Any] = {
        "tickCount": len(rows),
        "opening": (
            {"time": _sec_label(first[0]), "side": first[1], "volume": first[2], "amount": round(first[3])}
            if first else None
        ),
        "cutoffs": {_sec_label(sec)[:5] if sec % 60 == 0 else _sec_label(sec): summarize_until(rows, sec) for sec in cutoffs},
    }
    if raw:
        last = max(cutoffs, default=SESSION_END_SEC)
        data["merged"] = [
            [sec, side, volume, round(amount), count]
            for sec, side, volume, amount, count in merge_same_second(row for row in rows if row[0] <= last)
        ]
    return data


def _load_rows(service: Any, code: str, trade_date: str) -> list[tuple[int, int, int, float]]:
    today = datetime.now(TW_TZ).date().isoformat()
    key = (code, trade_date)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and (trade_date != today or now - cached[0] < _TODAY_TTL_SECONDS):
            _cache.move_to_end(key)
            return cached[1]
    contract = _resolve_stock_contract(service, code)
    if contract is None:
        raise LookupError("找不到股票合約")
    ticks = _fetch_historical_ticks(getattr(service, "api", None), contract, trade_date)
    rows = normalize_ticks(ticks, trade_date=trade_date)
    with _cache_lock:
        _cache[key] = (time.monotonic(), rows)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return rows


def recalc(
    service: Any,
    codes: Iterable[str],
    trade_date: str,
    cutoffs: Iterable[int],
    *,
    raw: bool = False,
    quota_check: Optional[Any] = None,
) -> dict[str, Any]:
    normalized = list(dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip()))
    if not normalized:
        raise ValueError("codes 不可空白")
    if len(normalized) > MAX_CODES:
        raise ValueError(f"一次最多 {MAX_CODES} 檔")
    cutoff_list = sorted(set(cutoffs)) or [parse_cutoff(value) for value in DEFAULT_CUTOFFS]
    api = getattr(service, "api", None)
    logged_in = bool(getattr(getattr(service, "state", None), "logged_in", False))
    if api is None or not logged_in:
        raise RuntimeError("Shioaji 尚未登入，無法抓歷史逐筆成交")
    items: dict[str, Any] = {}
    errors: dict[str, str] = {}
    with _query_lock:
        for code in normalized:
            if quota_check is not None:
                quota_error = quota_check(api)
                if quota_error:
                    errors[code] = str(quota_error)
                    continue
            try:
                rows = _load_rows(service, code, trade_date)
            except Exception as exc:
                errors[code] = str(exc) or exc.__class__.__name__
                continue
            items[code] = summarize_code(rows, cutoff_list, raw=raw)
    return {
        "status": "ok",
        "tradeDate": trade_date,
        "formula": "(大戶外盤買金額 − 大戶內盤賣金額) ÷ 累積成交金額 × 100；t=單筆張數、s=同秒同方向併單張數",
        "items": items,
        "errors": errors,
    }
