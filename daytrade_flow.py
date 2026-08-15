"""疑似隔日沖資金流回補與排行。

資料只使用 Shioaji 歷史逐筆成交，不含券商分點身分。大單門檻沿用
Market Data Hub（單筆 20 張或新台幣 100 萬元），讓盤中主力副圖與
隔日沖籌碼的判定口徑一致。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Any, Iterable, Mapping, Optional

from market_data_hub import _is_main_force_trade, _trade_side
from otc_index import TW_TZ, taipei_minute_of_day, taipei_trade_date
from stock_bar_bootstrap import (
    _fetch_historical_ticks,
    _field_values,
    _resolve_stock_contract,
    _shioaji_tick_to_ms,
)
from stock_groups import STOCK_GROUPS

logger = logging.getLogger("hanstock.daytrade_flow")

_CACHE_SECONDS = 30 * 60
_cache_lock = threading.RLock()
_scan_lock = threading.Lock()
_ranking_cache: dict[tuple[str, tuple[str, ...]], tuple[float, list[dict[str, Any]], list[str]]] = {}


def latest_completed_trade_date(now: Optional[datetime] = None) -> str:
    """回傳最近一個已收盤的平日；週末自動退回星期五。"""
    current = now.astimezone(TW_TZ) if now is not None else datetime.now(TW_TZ)
    candidate = current.date()
    if current.weekday() < 5 and current.time() < datetime_time(14, 0):
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.isoformat()


def resolve_trade_date(raw: Optional[str]) -> str:
    """驗證指定日期；未指定時取最近一個已收盤交易日。"""
    if raw is None or not str(raw).strip():
        return latest_completed_trade_date()
    value = str(raw).strip()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date 必須是 YYYY-MM-DD") from exc
    if parsed > datetime.now(TW_TZ).date():
        raise ValueError("不可查詢未來日期")
    return parsed.isoformat()


def candidate_codes(active_codes: Iterable[str] = ()) -> list[str]:
    """即時訂閱優先，再補族群清單；去除重複代號。"""
    result: list[str] = []
    seen: set[str] = set()
    sources: list[Iterable[Any]] = [active_codes]
    sources.extend(stocks for stocks in STOCK_GROUPS.values())
    for source in sources:
        for item in source:
            raw = item[0] if isinstance(item, (tuple, list)) else item
            code = str(raw).strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            result.append(code)
    return result


def _name_map() -> dict[str, str]:
    result: dict[str, str] = {}
    for stocks in STOCK_GROUPS.values():
        for code, name in stocks:
            result.setdefault(str(code).upper(), str(name))
    return result


def _market_label(service: Any, code: str) -> str:
    contract = _resolve_stock_contract(service, code)
    exchange = str(
        getattr(contract, "exchange", "")
        or getattr(contract, "exchange_code", "")
        or ""
    ).upper()
    if "OTC" in exchange or "OES" in exchange:
        return "上櫃"
    if "TSE" in exchange or "TWSE" in exchange:
        return "上市"
    return "上市櫃"


def summarize_historical_ticks(
    ticks: Any,
    *,
    ticker: str,
    name: str,
    market: str,
    trade_date: str,
) -> Optional[dict[str, Any]]:
    """把一日逐筆成交彙總成前端隔日沖模型需要的金額欄位。"""
    timestamps = _field_values(ticks, "ts", "datetime")
    closes = _field_values(ticks, "close", "Close")
    volumes = _field_values(ticks, "volume", "Volume")
    tick_types = _field_values(ticks, "tick_type", "TickType")
    amounts = _field_values(ticks, "amount", "Amount")
    simtrades = _field_values(ticks, "simtrade", "Simtrade")
    size = min(len(timestamps), len(closes), len(volumes))

    total_turnover_amount = 0.0
    large_buy_amount = 0.0
    large_sell_amount = 0.0
    late_large_buy_amount = 0.0
    first_price: Optional[float] = None
    last_price: Optional[float] = None

    for index in range(size):
        ts_ms = _shioaji_tick_to_ms(timestamps[index])
        if ts_ms is None or taipei_trade_date(ts_ms) != trade_date:
            continue
        minute = taipei_minute_of_day(ts_ms)
        if minute < 9 * 60 or minute > 13 * 60 + 30:
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

        raw_amount: Any = amounts[index] if index < len(amounts) else 0
        try:
            amount = float(raw_amount or 0)
        except (TypeError, ValueError, OverflowError):
            amount = 0.0
        if amount <= 0:
            amount = close * volume * 1000
        total_turnover_amount += amount
        if first_price is None:
            first_price = close
        last_price = close

        raw_tick_type: Any = tick_types[index] if index < len(tick_types) else 0
        raw_tick_type = getattr(raw_tick_type, "value", raw_tick_type)
        try:
            tick_type = int(raw_tick_type)
        except (TypeError, ValueError, OverflowError):
            tick_type = 0
        side = _trade_side({"tick_type": tick_type})
        if not _is_main_force_trade(
            {"close": close, "volume": volume, "amount": amount}
        ):
            continue
        if side == "buy":
            large_buy_amount += amount
            if minute >= 13 * 60:
                late_large_buy_amount += amount
        elif side == "sell":
            large_sell_amount += amount

    if total_turnover_amount <= 0 or (large_buy_amount <= 0 and large_sell_amount <= 0):
        return None
    price_impact_pct = 0.0
    if first_price and last_price:
        price_impact_pct = (last_price / first_price - 1) * 100
    return {
        "ticker": ticker,
        "name": name,
        "market": market,
        "trade_date": trade_date,
        "large_buy_amount": round(large_buy_amount),
        "large_sell_amount": round(large_sell_amount),
        "total_turnover_amount": round(total_turnover_amount),
        "late_large_buy_amount": round(late_large_buy_amount),
        "price_impact_pct": round(price_impact_pct, 4),
        # 第一個回補日尚無前一日／次一日配對；下一交易日收盤後再補算。
        "previous_large_buy_amount": 0,
        "next_day_large_sell_amount": 0,
    }


def _score(row: Mapping[str, Any]) -> float:
    turnover = max(1.0, float(row.get("total_turnover_amount", 0) or 0))
    buy = max(0.0, float(row.get("large_buy_amount", 0) or 0))
    sell = max(0.0, float(row.get("large_sell_amount", 0) or 0))
    late = max(0.0, float(row.get("late_large_buy_amount", 0) or 0))
    participation = buy / turnover
    net_rate = max(0.0, buy - sell) / turnover
    late_rate = late / buy if buy else 0.0
    impact = max(0.0, float(row.get("price_impact_pct", 0) or 0)) / 100
    return (
        min(participation / 0.25, 1.0) * 40
        + min(net_rate / 0.15, 1.0) * 25
        + min(late_rate / 0.80, 1.0) * 20
        + min(impact / 0.05, 1.0) * 15
    )


def scan_daytrade_flow(
    service: Any,
    *,
    trade_date: str,
    codes: Iterable[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """逐檔呼叫 Shioaji api.ticks(date=...)；結果快取 30 分鐘。"""
    normalized = tuple(
        dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip())
    )
    cache_key = (trade_date, normalized)
    now = time.monotonic()
    with _cache_lock:
        cached = _ranking_cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_SECONDS:
            return [dict(row) for row in cached[1]], list(cached[2])

    names = _name_map()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with _scan_lock:
        with _cache_lock:
            cached = _ranking_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
                return [dict(row) for row in cached[1]], list(cached[2])
        api = getattr(service, "api", None)
        logged_in = bool(getattr(getattr(service, "state", None), "logged_in", False))
        if api is None or not logged_in:
            raise RuntimeError("Shioaji 尚未登入，無法回補歷史逐筆成交")

        for code in normalized:
            try:
                contract = _resolve_stock_contract(service, code)
                if contract is None:
                    errors.append(f"{code}: 找不到股票合約")
                    continue
                ticks = _fetch_historical_ticks(api, contract, trade_date)
                row = summarize_historical_ticks(
                    ticks,
                    ticker=code,
                    name=names.get(code, code),
                    market=_market_label(service, code),
                    trade_date=trade_date,
                )
                if row is not None:
                    rows.append(row)
            except Exception as exc:
                logger.warning("[Daytrade Flow] %s %s 回補失敗: %s", trade_date, code, exc)
                errors.append(f"{code}: {exc}")

        rows.sort(
            key=lambda row: (_score(row), row["large_buy_amount"]),
            reverse=True,
        )
        with _cache_lock:
            _ranking_cache[cache_key] = (
                time.monotonic(),
                [dict(row) for row in rows],
                list(errors),
            )
    return rows, errors


def clear_daytrade_flow_cache() -> None:
    with _cache_lock:
        _ranking_cache.clear()
