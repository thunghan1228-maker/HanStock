"""疑似隔日沖資金流回補與排行。

資料只使用 Shioaji 歷史逐筆成交，不含券商分點身分。大單門檻沿用
Market Data Hub（單筆 20 張或新台幣 100 萬元），讓盤中主力副圖與
隔日沖籌碼的判定口徑一致。
"""

from __future__ import annotations

import logging
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Any, Iterable, Mapping, Optional

from market_data_hub import _is_main_force_trade, _trade_side
from main_force_store import load_main_force_bars
from otc_index import TW_TZ, taipei_minute_of_day, taipei_trade_date
from stock_bar_bootstrap import (
    _fetch_historical_ticks,
    _field_values,
    _resolve_stock_contract,
    _shioaji_tick_to_ms,
)
from stock_groups import STOCK_GROUPS
from daytrade_flow_store import (
    begin_daytrade_scan,
    fail_daytrade_scan,
    finish_daytrade_scan,
    load_daytrade_rows,
    load_daytrade_scan_status,
    save_daytrade_rows,
    update_daytrade_scan_progress,
)

logger = logging.getLogger("hanstock.daytrade_flow")

_CACHE_SECONDS = 30 * 60
_PARTIAL_RETRY_SECONDS = 30 * 60
_cache_lock = threading.RLock()
_scan_lock = threading.Lock()
_background_lock = threading.Lock()
_background_dates: set[str] = set()
_ranking_cache: dict[tuple[str, tuple[str, ...], bool], tuple[float, list[dict[str, Any]], list[str]]] = {}

TWSE_DAILY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_DAILY_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"


def _text(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def _number(value: Any) -> float:
    text = _text(value).replace(",", "").replace("＋", "+").replace("－", "-")
    if text in {"", "--", "---", "除權", "除息", "除權息"}:
        return 0.0
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else 0.0


def _fetch_json(url: str, params: Mapping[str, str]) -> Any:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": "HanStock-DaytradeFlow/2.0"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if not isinstance(payload, (dict, list)):
        raise RuntimeError("交易所日行情格式錯誤")
    return payload


def _tick_size(price: float) -> float:
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


def limit_up_price(reference_price: float) -> float:
    """台股一般股票 10% 漲停價，依價格級距向下取合法跳動單位。"""
    if reference_price <= 0:
        return 0.0
    raw = reference_price * 1.10
    tick = _tick_size(raw)
    return round(math.floor((raw + 1e-9) / tick) * tick, 2)


def _daily_row(
    *,
    ticker: str,
    name: str,
    market: str,
    open_price: float,
    high_price: float,
    close_price: float,
    reference_price: float,
    turnover: float,
) -> Optional[dict[str, Any]]:
    code = ticker.strip().upper()
    if not code or open_price <= 0 or high_price <= 0 or close_price <= 0 or reference_price <= 0:
        return None
    upper = limit_up_price(reference_price)
    if upper <= 0:
        return None
    return {
        "ticker": code,
        "name": name.strip() or code,
        "market": market,
        "open_price": open_price,
        "high_price": high_price,
        "close_price": close_price,
        "reference_price": reference_price,
        "limit_up_price": upper,
        "day_change_pct": round((close_price / reference_price - 1) * 100, 4),
        "official_turnover_amount": max(0.0, turnover),
        "reached_limit_up": high_price + 1e-8 >= upper,
        "closed_at_limit_up": close_price + 1e-8 >= upper,
    }


def _fetch_archived_daily_market_snapshot(trade_date: str) -> list[dict[str, Any]]:
    """讀取 TWSE/TPEx 官方收盤行情，建立全市場掃描母名單。"""
    target = date.fromisoformat(trade_date)
    rows: list[dict[str, Any]] = []

    twse = _fetch_json(
        TWSE_DAILY_URL,
        {"date": target.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
    )
    for table in twse.get("tables") or []:
        fields = [_text(item) for item in table.get("fields") or []]
        if "證券代號" not in fields or "收盤價" not in fields or "漲跌價差" not in fields:
            continue
        indexes = {field: index for index, field in enumerate(fields)}
        for values in table.get("data") or []:
            if not isinstance(values, list):
                continue
            def value(field: str) -> Any:
                index = indexes.get(field, -1)
                return values[index] if 0 <= index < len(values) else ""
            close = _number(value("收盤價"))
            change = _number(value("漲跌價差"))
            sign = _text(value("漲跌(+/-)"))
            signed_change = -abs(change) if "-" in sign or "－" in sign else abs(change)
            daily = _daily_row(
                ticker=_text(value("證券代號")),
                name=_text(value("證券名稱")),
                market="上市",
                open_price=_number(value("開盤價")),
                high_price=_number(value("最高價")),
                close_price=close,
                reference_price=close - signed_change,
                turnover=_number(value("成交金額")),
            )
            if daily:
                rows.append(daily)

    tpex = _fetch_json(
        TPEX_DAILY_URL,
        {"date": target.strftime("%Y/%m/%d"), "id": "", "response": "json"},
    )
    for table in tpex.get("tables") or []:
        fields = [_text(item) for item in table.get("fields") or []]
        if "代號" not in fields or "收盤" not in fields or "漲跌" not in fields:
            continue
        indexes = {field: index for index, field in enumerate(fields)}
        for values in table.get("data") or []:
            if not isinstance(values, list):
                continue
            def value(field: str) -> Any:
                index = indexes.get(field, -1)
                return values[index] if 0 <= index < len(values) else ""
            close = _number(value("收盤"))
            change = _number(value("漲跌"))
            daily = _daily_row(
                ticker=_text(value("代號")),
                name=_text(value("名稱")),
                market="上櫃",
                open_price=_number(value("開盤")),
                high_price=_number(value("最高")),
                close_price=close,
                reference_price=close - change,
                turnover=_number(value("成交金額(元)")),
            )
            if daily:
                rows.append(daily)

    deduplicated: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduplicated.setdefault(str(row["ticker"]), row)
    if not deduplicated:
        raise RuntimeError(f"{trade_date} 交易所收盤行情為空，不覆蓋既有備份")
    return list(deduplicated.values())


def _roc_date(trade_date: str) -> str:
    parsed = date.fromisoformat(trade_date)
    return f"{parsed.year - 1911:03d}{parsed.month:02d}{parsed.day:02d}"


def _fetch_current_openapi_snapshot(trade_date: str) -> list[dict[str, Any]]:
    """交易所歷史端點被機房擋下時，改走官方 OpenAPI 最近交易日備援。"""
    expected = _roc_date(trade_date)
    rows: list[dict[str, Any]] = []
    sources = (
        ("上市", "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"),
        ("上櫃", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"),
    )
    for market, url in sources:
        payload = _fetch_json(url, {})
        raw_rows = payload if isinstance(payload, list) else []
        for raw in raw_rows:
            if not isinstance(raw, dict) or str(raw.get("Date") or "") != expected:
                continue
            if market == "上市":
                close = _number(raw.get("ClosingPrice"))
                change = _number(raw.get("Change"))
                daily = _daily_row(
                    ticker=_text(raw.get("Code")), name=_text(raw.get("Name")), market=market,
                    open_price=_number(raw.get("OpeningPrice")), high_price=_number(raw.get("HighestPrice")),
                    close_price=close, reference_price=close - change,
                    turnover=_number(raw.get("TradeValue")),
                )
            else:
                close = _number(raw.get("Close"))
                change = _number(raw.get("Change"))
                daily = _daily_row(
                    ticker=_text(raw.get("SecuritiesCompanyCode")), name=_text(raw.get("CompanyName")), market=market,
                    open_price=_number(raw.get("Open")), high_price=_number(raw.get("High")),
                    close_price=close, reference_price=close - change,
                    turnover=_number(raw.get("TransactionAmount")),
                )
            if daily:
                rows.append(daily)
    if not rows:
        raise RuntimeError(f"{trade_date} 官方 OpenAPI 尚無相符交易日，不覆蓋既有備份")
    return list({str(row["ticker"]): row for row in rows}.values())


def fetch_daily_market_snapshot(trade_date: str) -> list[dict[str, Any]]:
    try:
        return _fetch_archived_daily_market_snapshot(trade_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("交易所歷史端點失敗，改走官方 OpenAPI 備援: %s", exc)
        return _fetch_current_openapi_snapshot(trade_date)


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


def is_equity_code(code: str) -> bool:
    """排除 ETF、債券、權證等；保留一般股票、特別股與 TDR。"""
    value = str(code or "").strip().upper()
    return bool(re.fullmatch(r"(?:[1-9]\d{3}[A-Z]?|91\d{4})", value))


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
    daily_meta: Optional[Mapping[str, Any]] = None,
    include_unclassified: bool = False,
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
    valid_tick_count = 0
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

        valid_tick_count += 1
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

    if valid_tick_count <= 0:
        return None

    reached_limit_up = bool((daily_meta or {}).get("reached_limit_up"))
    if total_turnover_amount <= 0:
        total_turnover_amount = float((daily_meta or {}).get("official_turnover_amount") or 0)
    if total_turnover_amount <= 0 or (
        not include_unclassified
        and large_buy_amount <= 0 and large_sell_amount <= 0 and not reached_limit_up
    ):
        return None
    price_impact_pct = 0.0
    if first_price and last_price:
        price_impact_pct = (last_price / first_price - 1) * 100
    row = {
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
        "main_force_data_available": True,
        "main_force_data_status": "historical_ticks",
    }
    if daily_meta:
        for field in (
            "open_price",
            "high_price",
            "close_price",
            "reference_price",
            "limit_up_price",
            "day_change_pct",
            "reached_limit_up",
            "closed_at_limit_up",
        ):
            row[field] = daily_meta.get(field, 0)
    row["suspicion_score"] = round(_score(row), 2)
    row["category"] = classify_daytrade_row(row)
    return row if include_unclassified or row["category"] else None


def summarize_persisted_main_force_bars(
    bars: Iterable[Mapping[str, Any]],
    *,
    ticker: str,
    name: str,
    market: str,
    trade_date: str,
    daily_meta: Optional[Mapping[str, Any]] = None,
    include_unclassified: bool = False,
) -> Optional[dict[str, Any]]:
    """歷史 ticks 尚未釋出時，以盤中永久保存的 1 分主力金額回補。"""
    valid: list[Mapping[str, Any]] = []
    for bar in bars:
        if not isinstance(bar, Mapping) or not bool(bar.get("main_force_available")):
            continue
        if str(bar.get("trade_date") or trade_date) != trade_date:
            continue
        valid.append(bar)
    if not valid:
        return None

    large_buy_amount = sum(max(0.0, float(bar.get("main_buy_amount") or 0)) for bar in valid)
    large_sell_amount = sum(max(0.0, float(bar.get("main_sell_amount") or 0)) for bar in valid)
    late_large_buy_amount = 0.0
    for bar in valid:
        try:
            minute = taipei_minute_of_day(int(bar.get("ts") or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if minute >= 13 * 60:
            late_large_buy_amount += max(0.0, float(bar.get("main_buy_amount") or 0))

    meta = daily_meta or {}
    turnover = max(0.0, float(meta.get("official_turnover_amount") or 0))
    reached_limit_up = bool(meta.get("reached_limit_up"))
    if turnover <= 0 or (
        not include_unclassified
        and large_buy_amount <= 0 and large_sell_amount <= 0 and not reached_limit_up
    ):
        return None
    open_price = max(0.0, float(meta.get("open_price") or 0))
    close_price = max(0.0, float(meta.get("close_price") or 0))
    price_impact_pct = (close_price / open_price - 1) * 100 if open_price > 0 and close_price > 0 else 0.0
    row = {
        "ticker": ticker,
        "name": name,
        "market": market,
        "trade_date": trade_date,
        "large_buy_amount": round(large_buy_amount),
        "large_sell_amount": round(large_sell_amount),
        "total_turnover_amount": round(turnover),
        "late_large_buy_amount": round(late_large_buy_amount),
        "price_impact_pct": round(price_impact_pct, 4),
        "previous_large_buy_amount": 0,
        "next_day_large_sell_amount": 0,
        "main_force_data_available": True,
        "main_force_data_status": "persisted_intraday_bars",
    }
    for field in (
        "open_price", "high_price", "close_price", "reference_price",
        "limit_up_price", "day_change_pct", "reached_limit_up", "closed_at_limit_up",
    ):
        row[field] = meta.get(field, 0)
    row["suspicion_score"] = round(_score(row), 2)
    row["category"] = classify_daytrade_row(row)
    return row if include_unclassified or row["category"] else None


def missing_main_force_row(
    *,
    ticker: str,
    name: str,
    market: str,
    trade_date: str,
    daily_meta: Optional[Mapping[str, Any]] = None,
    include_unclassified: bool = False,
) -> Optional[dict[str, Any]]:
    """保留官方漲停候選，但明確標成待回補，禁止把缺資料冒充成 0。"""
    meta = daily_meta or {}
    if not include_unclassified and not bool(meta.get("reached_limit_up")):
        return None
    turnover = max(0.0, float(meta.get("official_turnover_amount") or 0))
    if turnover <= 0:
        return None
    row = {
        "ticker": ticker,
        "name": name,
        "market": market,
        "trade_date": trade_date,
        "large_buy_amount": 0,
        "large_sell_amount": 0,
        "total_turnover_amount": round(turnover),
        "late_large_buy_amount": 0,
        "price_impact_pct": 0,
        "previous_large_buy_amount": 0,
        "next_day_large_sell_amount": 0,
        "suspicion_score": 0,
        "main_force_data_available": False,
        "main_force_data_status": "pending_backfill",
    }
    for field in (
        "open_price", "high_price", "close_price", "reference_price",
        "limit_up_price", "day_change_pct", "reached_limit_up", "closed_at_limit_up",
    ):
        row[field] = meta.get(field, 0)
    row["category"] = classify_daytrade_row(row)
    return row if include_unclassified or row["category"] else None


def summarize_daytrade_sources(
    ticks: Any,
    *,
    ticker: str,
    name: str,
    market: str,
    trade_date: str,
    daily_meta: Optional[Mapping[str, Any]] = None,
    include_unclassified: bool = False,
) -> Optional[dict[str, Any]]:
    """歷史 ticks 優先、盤中永久資料備援，最後才回傳待回補狀態。"""
    row = summarize_historical_ticks(
        ticks, ticker=ticker, name=name, market=market,
        trade_date=trade_date, daily_meta=daily_meta,
        include_unclassified=include_unclassified,
    )
    if row is not None:
        return row
    try:
        persisted = load_main_force_bars(ticker, "1m", trade_date=trade_date, limit=1000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Daytrade Flow] %s %s 永久主力資料讀取失敗: %s", trade_date, ticker, exc)
        persisted = []
    row = summarize_persisted_main_force_bars(
        persisted, ticker=ticker, name=name, market=market,
        trade_date=trade_date, daily_meta=daily_meta,
        include_unclassified=include_unclassified,
    )
    if row is not None:
        return row
    return missing_main_force_row(
        ticker=ticker, name=name, market=market,
        trade_date=trade_date, daily_meta=daily_meta,
        include_unclassified=include_unclassified,
    )


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


def classify_daytrade_row(row: Mapping[str, Any]) -> str:
    """三層名單：優先看真實漲停，再看強勢大單，不列一般交易。"""
    if bool(row.get("closed_at_limit_up")):
        return "漲停鎖定"
    if bool(row.get("reached_limit_up")):
        return "曾達漲停"
    buy = float(row.get("large_buy_amount") or 0)
    sell = float(row.get("large_sell_amount") or 0)
    score = float(row.get("suspicion_score") or _score(row))
    day_change = float(row.get("day_change_pct") or row.get("price_impact_pct") or 0)
    if buy > sell and day_change > 0 and score >= 30:
        return "強勢大單"
    return ""


def scan_daytrade_flow(
    service: Any,
    *,
    trade_date: str,
    codes: Iterable[str],
    daily_rows: Optional[Iterable[Mapping[str, Any]]] = None,
    include_unclassified: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """逐檔呼叫 Shioaji api.ticks(date=...)；結果快取 30 分鐘。"""
    normalized = tuple(
        dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip())
    )
    cache_key = (trade_date, normalized, include_unclassified)
    now = time.monotonic()
    with _cache_lock:
        cached = _ranking_cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_SECONDS:
            return [dict(row) for row in cached[1]], list(cached[2])

    market_rows = list(daily_rows) if daily_rows is not None else fetch_daily_market_snapshot(trade_date)
    daily_by_code = {str(row.get("ticker") or "").upper(): row for row in market_rows}
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
                row = summarize_daytrade_sources(
                    ticks,
                    ticker=code,
                    name=str((daily_by_code.get(code) or {}).get("name") or names.get(code, code)),
                    market=str((daily_by_code.get(code) or {}).get("market") or _market_label(service, code)),
                    trade_date=trade_date,
                    daily_meta=daily_by_code.get(code),
                    include_unclassified=include_unclassified,
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


def _full_market_worker(service: Any, trade_date: str, force: bool) -> None:
    try:
        daily_rows = fetch_daily_market_snapshot(trade_date)
        # 只保留 Shioaji 能解析為一般股票的合約；排除權證、牛熊證等商品。
        candidates: list[dict[str, Any]] = []
        for row in daily_rows:
            code = str(row.get("ticker") or "").strip().upper()
            if is_equity_code(code) and _resolve_stock_contract(service, code) is not None:
                candidates.append(row)
        if not candidates:
            raise RuntimeError(f"{trade_date} 找不到可掃描的股票合約")

        begin_daytrade_scan(trade_date, len(candidates))
        api = getattr(service, "api", None)
        logged_in = bool(getattr(getattr(service, "state", None), "logged_in", False))
        if api is None or not logged_in:
            raise RuntimeError("Shioaji 尚未登入，稍後自動重試")

        matches: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, daily in enumerate(candidates, start=1):
            code = str(daily["ticker"])
            try:
                contract = _resolve_stock_contract(service, code)
                if contract is None:
                    continue
                ticks = _fetch_historical_ticks(api, contract, trade_date)
                row = summarize_daytrade_sources(
                    ticks,
                    ticker=code,
                    name=str(daily.get("name") or code),
                    market=str(daily.get("market") or "上市櫃"),
                    trade_date=trade_date,
                    daily_meta=daily,
                )
                if row is not None:
                    matches.append(row)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Daytrade Full Scan] %s %s 失敗: %s", trade_date, code, exc)
                errors.append(f"{code}: {exc}")

            if index % 20 == 0 or index == len(candidates):
                # 掃描途中也增量保存；容器重啟時已完成部分不會消失。
                save_daytrade_rows(matches[-20:])
                update_daytrade_scan_progress(
                    trade_date,
                    processed_count=index,
                    match_count=len(matches),
                    errors=errors,
                )

        matches.sort(
            key=lambda row: (
                {"漲停鎖定": 3, "曾達漲停": 2, "強勢大單": 1}.get(str(row.get("category")), 0),
                float(row.get("suspicion_score") or 0),
                float(row.get("large_buy_amount") or 0),
            ),
            reverse=True,
        )
        missing_count = sum(
            1 for row in matches if not bool(row.get("main_force_data_available", True))
        )
        finish_daytrade_scan(
            trade_date,
            matches,
            processed_count=len(candidates),
            errors=errors,
            incomplete_count=missing_count,
        )
        clear_daytrade_flow_cache()
        logger.info(
            "[Daytrade Full Scan] 完成 date=%s stocks=%s matches=%s pending=%s errors=%s",
            trade_date,
            len(candidates),
            len(matches),
            missing_count,
            len(errors),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[Daytrade Full Scan] %s 全市場掃描失敗", trade_date)
        fail_daytrade_scan(trade_date, str(exc))
    finally:
        with _background_lock:
            _background_dates.discard(trade_date)


def start_full_market_scan(service: Any, trade_date: str, *, force: bool = False) -> bool:
    """非同步啟動全市場掃描；同一天只允許一條執行緒。"""
    status = load_daytrade_scan_status(trade_date)
    if not force:
        if status.get("status") == "completed":
            return False
        if status.get("status") == "partial":
            try:
                updated_at = datetime.fromisoformat(str(status.get("updated_at") or ""))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=TW_TZ)
                if (datetime.now(TW_TZ) - updated_at.astimezone(TW_TZ)).total_seconds() < _PARTIAL_RETRY_SECONDS:
                    return False
            except ValueError:
                pass
    with _background_lock:
        if trade_date in _background_dates:
            return False
        _background_dates.add(trade_date)
    thread = threading.Thread(
        target=_full_market_worker,
        args=(service, trade_date, force),
        name=f"hanstock-daytrade-{trade_date}",
        daemon=True,
    )
    thread.start()
    return True


def daytrade_flow_snapshot(trade_date: str, limit: int = 500) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return load_daytrade_rows(trade_date, limit=limit), load_daytrade_scan_status(trade_date)


def clear_daytrade_flow_cache() -> None:
    with _cache_lock:
        _ranking_cache.clear()
