"""台指期 5 分 K 重啟復原層。

Shioaji 歷史 1 分 Kbars 負責補齊服務啟動前資料，MarketDataHub 的
即時期貨 Tick 聚合負責目前 K 棒；同 timestamp 一律以即時資料覆蓋。
期貨使用自己的日盤／夜盤時段，絕不套用股票 09:00～13:30 規則。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional

from otc_index import FIVE_MIN_MS, ONE_MIN_MS, TW_TZ, shioaji_kbar_close_to_start_ms

logger = logging.getLogger("hanstock.futures_bar_bootstrap")
RETRY_AFTER_SECONDS = 30.0
NIGHT_SESSION_PREFIXES = ("TXF", "MXF", "TMF")


@dataclass
class _SessionWindow:
    name: str
    start_ms: int
    end_ms: int


@dataclass
class _HistoryEntry:
    session_start_ms: int
    code: str
    bars_1m: list[dict[str, Any]]
    bars_5m: list[dict[str, Any]]
    fetched_at_monotonic: float
    ok: bool
    error: Optional[str] = None


_cache_lock = threading.RLock()
_history_cache: dict[tuple[str, int], _HistoryEntry] = {}
_code_locks: dict[tuple[str, int], threading.Lock] = {}


def clear_futures_bar_bootstrap_cache() -> None:
    with _cache_lock:
        _history_cache.clear()
        _code_locks.clear()


def _default_service() -> Any:
    from quote_service import get_quote_service

    return get_quote_service()


def _default_hub() -> Any:
    from market_data_hub import get_market_data_hub

    return get_market_data_hub()


def _has_night_session(code: str) -> bool:
    normalized = str(code).strip().upper()
    return normalized.startswith(NIGHT_SESSION_PREFIXES)


def _previous_weekday(value: datetime) -> datetime:
    current = value
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def _day_only_session_window(now_ms: int) -> _SessionWindow:
    """股票期貨／小型股票期貨只顯示最近的 08:45～13:45 日盤。"""
    now = datetime.fromtimestamp(now_ms / 1000, TW_TZ)
    minute = now.hour * 60 + now.minute
    session_day = now.replace(hour=8, minute=45, second=0, microsecond=0)
    if now.weekday() >= 5 or minute < 8 * 60 + 45:
        session_day = _previous_weekday(session_day - timedelta(days=1))
    start = session_day
    end = session_day.replace(hour=13, minute=45)
    return _SessionWindow("day", int(start.timestamp() * 1000), int(end.timestamp() * 1000))


def _session_window(now_ms: int, futures_code: str = "TXFR1") -> _SessionWindow:
    """回傳目前（休市時為最近一個）該期貨商品交易時段。"""
    if not _has_night_session(futures_code):
        return _day_only_session_window(now_ms)
    now = datetime.fromtimestamp(now_ms / 1000, TW_TZ)
    minute = now.hour * 60 + now.minute
    day = now.replace(hour=8, minute=45, second=0, microsecond=0)
    night = now.replace(hour=15, minute=0, second=0, microsecond=0)

    if 8 * 60 + 45 <= minute < 13 * 60 + 45:
        start, end, name = day, day.replace(hour=13, minute=45), "day"
    elif minute >= 15 * 60:
        start, end, name = night, night + timedelta(hours=14), "night"
    elif minute < 5 * 60:
        start = night - timedelta(days=1)
        end, name = start + timedelta(hours=14), "night"
    elif minute < 8 * 60 + 45:
        # 05:00～08:45 顯示剛結束的夜盤。
        start = night - timedelta(days=1)
        end, name = start + timedelta(hours=14), "night"
    else:
        # 13:45～15:00 顯示剛結束的日盤。
        start, end, name = day, day.replace(hour=13, minute=45), "day"

    return _SessionWindow(name, int(start.timestamp() * 1000), int(end.timestamp() * 1000))


def _safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == result else None


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _field(kbars: Any, *names: str) -> list[Any]:
    for name in names:
        value = kbars.get(name) if isinstance(kbars, Mapping) else getattr(kbars, name, None)
        if value is not None:
            try:
                return list(value)
            except TypeError:
                return []
    return []


def _normalize_1m(
    kbars: Any,
    *,
    window: _SessionWindow,
    now_ms: int,
) -> list[dict[str, Any]]:
    timestamps = _field(kbars, "ts", "datetime")
    opens = _field(kbars, "Open", "open")
    highs = _field(kbars, "High", "high")
    lows = _field(kbars, "Low", "low")
    closes = _field(kbars, "Close", "close")
    volumes = _field(kbars, "Volume", "volume")
    size = min(len(timestamps), len(opens), len(highs), len(lows), len(closes))
    current_minute = now_ms - (now_ms % ONE_MIN_MS)
    rows: dict[int, dict[str, Any]] = {}

    for index in range(size):
        ts = shioaji_kbar_close_to_start_ms(timestamps[index])
        if ts is None or ts < window.start_ms or ts >= window.end_ms or ts >= current_minute:
            continue
        values = tuple(_safe_float(field[index]) for field in (opens, highs, lows, closes))
        if any(value is None or value <= 0 for value in values):
            continue
        open_, high, low, close = values
        rows[ts] = {
            "ts": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": _safe_int(volumes[index] if index < len(volumes) else 0),
            "tick_count": 1,
        }
    return [rows[ts] for ts in sorted(rows)]


def _aggregate_5m(bars: list[dict[str, Any]], *, now_ms: int) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for bar in bars:
        bucket = int(bar["ts"]) - (int(bar["ts"]) % FIVE_MIN_MS)
        grouped.setdefault(bucket, []).append(bar)

    current_bucket = now_ms - (now_ms % FIVE_MIN_MS)
    result: list[dict[str, Any]] = []
    for bucket in sorted(grouped):
        if bucket >= current_bucket:
            continue
        rows = sorted(grouped[bucket], key=lambda row: int(row["ts"]))
        result.append({
            "ts": bucket,
            "open": rows[0]["open"],
            "high": max(row["high"] for row in rows),
            "low": min(row["low"] for row in rows),
            "close": rows[-1]["close"],
            "volume": sum(_safe_int(row.get("volume")) for row in rows),
            "tick_count": sum(max(1, _safe_int(row.get("tick_count"))) for row in rows),
        })
    return result


def _looks_like_futures_contract(contract: Any) -> bool:
    if contract is None:
        return False
    security_type = str(getattr(contract, "security_type", "") or "").upper()
    if security_type and ("STK" in security_type or "STOCK" in security_type) and not (
        "FUT" in security_type or "FOP" in security_type
    ):
        return False
    return bool(str(getattr(contract, "code", "") or getattr(contract, "target_code", "")).strip())


def _lookup_api_contract(api: Any, code: str) -> Any:
    contract = None
    try:
        contract = api.contracts.get(code)
    except Exception:
        contract = None
    if _looks_like_futures_contract(contract):
        return contract

    futures = getattr(getattr(api, "Contracts", None), "Futures", None)
    if futures is None:
        return None
    prefixes: list[str] = []
    for prefix in (code[:3], code[:2], code[:4], code[:5], *NIGHT_SESSION_PREFIXES):
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    for prefix in prefixes:
        group = None
        try:
            group = futures[prefix]
        except Exception:
            group = getattr(futures, prefix, None)
        if group is None:
            continue
        try:
            contract = group[code]
        except Exception:
            contract = getattr(group, code, None)
        if _looks_like_futures_contract(contract):
            return contract
    return None


def _default_stock_futures_contract_lookup(code: str) -> Optional[tuple[Any, str]]:
    try:
        from stock_futures_service import get_stock_futures_quote_service

        return get_stock_futures_quote_service().resolve_contract_by_code(code)
    except Exception:
        return None


def _resolve_contract(
    service: Any,
    requested_code: str,
    *,
    stock_futures_lookup: Optional[Callable[[str], Optional[tuple[Any, str]]]] = None,
) -> tuple[Any, str]:
    api = getattr(service, "api", None)
    if api is None:
        return None, requested_code

    lookup = stock_futures_lookup or _default_stock_futures_contract_lookup
    if not _has_night_session(requested_code):
        mapped = lookup(requested_code)
        if mapped is not None:
            contract, canonical = mapped
            if _looks_like_futures_contract(contract):
                return contract, str(canonical or requested_code).strip().upper()

    candidates: list[str] = []
    # TXF 畫面可能仍帶到舊交割月代號；應優先使用主行情連線已解析的
    # R1／target_code，避免拿過期合約查今天 Kbars 而得到 0 根。
    if requested_code.startswith("TXF"):
        for raw in (getattr(service, "_target_code", None), getattr(service, "_resolved_futures_code", None)):
            candidate = str(raw or "").strip().upper()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    if requested_code not in candidates:
        candidates.append(requested_code)

    contract = None
    for candidate in candidates:
        contract = _lookup_api_contract(api, candidate)
        if contract is not None:
            break

    canonical = str(
        getattr(contract, "target_code", None)
        or getattr(contract, "code", None)
        or requested_code
    ).strip().upper()
    if contract is not None and _has_night_session(requested_code):
        ensure_subscription = getattr(service, "ensure_extra_futures_subscription", None)
        if callable(ensure_subscription):
            try:
                ensure_subscription(contract)
            except Exception as exc:
                logger.debug("[Futures KBar] %s 即時訂閱失敗，保留歷史 Kbars: %s", canonical, exc)
    return contract, canonical


def _bootstrap_history(
    requested_code: str,
    window: _SessionWindow,
    *,
    service: Any,
    now_ms: int,
    monotonic_fn: Callable[[], float],
    stock_futures_lookup: Optional[Callable[[str], Optional[tuple[Any, str]]]] = None,
) -> _HistoryEntry:
    contract, canonical = _resolve_contract(
        service,
        requested_code,
        stock_futures_lookup=stock_futures_lookup,
    )
    api = getattr(service, "api", None)
    logged_in = bool(getattr(getattr(service, "state", None), "logged_in", False))
    if api is None or not logged_in or contract is None:
        error = "Shioaji 尚未登入" if api is None or not logged_in else f"找不到期貨合約：{requested_code}"
        return _HistoryEntry(window.start_ms, canonical, [], [], monotonic_fn(), False, error)

    try:
        start_date = datetime.fromtimestamp(window.start_ms / 1000, TW_TZ).strftime("%Y-%m-%d")
        end_date = datetime.fromtimestamp(min(now_ms, window.end_ms) / 1000, TW_TZ).strftime("%Y-%m-%d")
        kbars = api.kbars(contract=contract, start=start_date, end=end_date)
        bars_1m = _normalize_1m(kbars, window=window, now_ms=now_ms)
        bars_5m = _aggregate_5m(bars_1m, now_ms=now_ms)
        ok = bool(bars_1m)
        logger.info("[Futures KBar] %s 補齊完成: 1m=%d, 5m=%d, session=%s", canonical, len(bars_1m), len(bars_5m), window.name)
        return _HistoryEntry(
            window.start_ms,
            canonical,
            bars_1m,
            bars_5m,
            monotonic_fn(),
            ok,
            None if ok else "Shioaji 本交易時段 Kbars 暫無資料",
        )
    except Exception as exc:
        logger.warning("[Futures KBar] %s 歷史補齊失敗: %s", requested_code, exc)
        return _HistoryEntry(window.start_ms, canonical, [], [], monotonic_fn(), False, str(exc))


def _safe_bar(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    try:
        bar = {
            "ts": int(raw.get("ts")),
            "open": float(raw.get("open")),
            "high": float(raw.get("high")),
            "low": float(raw.get("low")),
            "close": float(raw.get("close")),
            "volume": _safe_int(raw.get("volume")),
            "tick_count": _safe_int(raw.get("tick_count")),
        }
    except (TypeError, ValueError, OverflowError):
        return None
    return bar if bar["ts"] > 0 and min(bar["open"], bar["high"], bar["low"], bar["close"]) > 0 else None


def _merge(history: list[dict[str, Any]], live: list[dict[str, Any]], window: _SessionWindow) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for source in (history, live):
        for raw in source:
            bar = _safe_bar(raw)
            if bar and window.start_ms <= bar["ts"] < window.end_ms:
                merged[bar["ts"]] = bar
    return [merged[ts] for ts in sorted(merged)]


def get_resilient_futures_bars(
    futures_code: str,
    interval: str = "5m",
    *,
    service: Any = None,
    hub: Any = None,
    now_ms: Optional[int] = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
    stock_futures_lookup: Optional[Callable[[str], Optional[tuple[Any, str]]]] = None,
) -> dict[str, Any]:
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    requested_code = str(futures_code).strip().upper()
    now_value = now_ms if now_ms is not None else int(datetime.now(TW_TZ).timestamp() * 1000)
    window = _session_window(now_value, requested_code)
    service = service if service is not None else _default_service()
    hub = hub if hub is not None else _default_hub()
    key = (requested_code, window.start_ms)

    with _cache_lock:
        entry = _history_cache.get(key)
        if entry and not entry.ok and monotonic_fn() - entry.fetched_at_monotonic >= RETRY_AFTER_SECONDS:
            entry = None
        lock = _code_locks.setdefault(key, threading.Lock())
    if entry is None:
        with lock:
            with _cache_lock:
                entry = _history_cache.get(key)
            if entry is None or (not entry.ok and monotonic_fn() - entry.fetched_at_monotonic >= RETRY_AFTER_SECONDS):
                entry = _bootstrap_history(
                    requested_code,
                    window,
                    service=service,
                    now_ms=now_value,
                    monotonic_fn=monotonic_fn,
                    stock_futures_lookup=stock_futures_lookup,
                )
                with _cache_lock:
                    _history_cache[key] = entry

    live_getter = hub.get_live_futures_bars_1m if interval == "1m" else hub.get_live_futures_bars
    live = list(live_getter(entry.code) or [])
    if entry.code != requested_code and not live:
        live = list(live_getter(requested_code) or [])
    history = entry.bars_1m if interval == "1m" else entry.bars_5m
    bars = _merge(history, live, window)
    return {
        "status": "ok",
        "requested_code": requested_code,
        "code": entry.code,
        "interval": interval,
        "session": window.name,
        "bar_count": len(bars),
        "bars": bars,
        "bootstrap": {
            "session_start": datetime.fromtimestamp(window.start_ms / 1000, TW_TZ).isoformat(),
            "session_end": datetime.fromtimestamp(window.end_ms / 1000, TW_TZ).isoformat(),
            "history_1m": len(entry.bars_1m),
            "history_5m": len(entry.bars_5m),
            "live_count": len(live),
            "history_ok": entry.ok,
            "error": entry.error,
            "source": "shioaji_futures_kbars+realtime_hub",
        },
    }
