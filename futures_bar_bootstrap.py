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
from typing import Any, Callable, Iterable, Mapping, Optional

from otc_index import FIVE_MIN_MS, ONE_MIN_MS, TW_TZ, shioaji_kbar_close_to_start_ms

logger = logging.getLogger("hanstock.futures_bar_bootstrap")
RETRY_AFTER_SECONDS = 30.0
NIGHT_SESSION_PREFIXES = ("TXF", "MXF", "TMF")
MAX_KBARS_CALENDAR_DAYS = 30


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
    name: str = ""


_cache_lock = threading.RLock()
_history_cache: dict[tuple[str, int, int], _HistoryEntry] = {}
_code_locks: dict[tuple[str, int, int], threading.Lock] = {}


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


def _aggregate_1d(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """將完成的 1 分 K 依台北日盤日期聚合成日 K。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for bar in bars:
        day = datetime.fromtimestamp(int(bar["ts"]) / 1000, TW_TZ).strftime("%Y-%m-%d")
        grouped.setdefault(day, []).append(bar)

    result: list[dict[str, Any]] = []
    for day in sorted(grouped):
        rows = sorted(grouped[day], key=lambda row: int(row["ts"]))
        result.append({
            "ts": rows[0]["ts"],
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
    """在指定 Shioaji instance 上取得合約；先讀本機 catalog 再打遠端。

    冷啟動時 ``api.contracts.get`` 可能因 P2P session 尚未建立而失敗，
    但 ``api.Contracts.Futures`` 通常已經可用。歷史 Kbars 又不能沿用別的
    Shioaji instance 建立的 contract，所以這裡必須優先找目前 API 自己的
    catalog 合約。
    """
    futures = getattr(getattr(api, "Contracts", None), "Futures", None)
    if futures is not None:
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

    # local catalog 裡真的沒有時才使用遠端查詢，避免冷啟動的
    # SessionNotEstablished 擋住原本已就緒的 Futures catalog。
    contract = None
    try:
        contract = api.contracts.get(code)
    except Exception:
        contract = None
    return contract if _looks_like_futures_contract(contract) else None


def _lookup_api_contract_candidates(api: Any, *codes: Any) -> Any:
    seen: set[str] = set()
    for raw in codes:
        code = str(raw or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        try:
            contract = _lookup_api_contract(api, code)
        except Exception:
            contract = None
        if _looks_like_futures_contract(contract):
            return contract
    return None


def _default_stock_futures_contract_lookup(code: str) -> Optional[tuple[Any, str, Any]]:
    try:
        from stock_futures_service import get_stock_futures_quote_service

        return get_stock_futures_quote_service().resolve_contract_context_by_code(code)
    except Exception:
        return None


def _default_stock_futures_history_apis() -> Iterable[Any]:
    try:
        from stock_futures_service import get_stock_futures_quote_service

        return get_stock_futures_quote_service().history_api_candidates()
    except Exception:
        return []


def _resolve_contract(
    service: Any,
    requested_code: str,
    *,
    stock_futures_lookup: Optional[Callable[[str], Optional[tuple[Any, str]]]] = None,
) -> tuple[Any, str, Any]:
    api = getattr(service, "api", None)
    if api is None:
        return None, requested_code, None

    lookup = stock_futures_lookup or _default_stock_futures_contract_lookup
    if not _has_night_session(requested_code):
        mapped = lookup(requested_code)
        if mapped is not None:
            contract, canonical = mapped[:2]
            contract_api = mapped[2] if len(mapped) > 2 else api
            if _looks_like_futures_contract(contract):
                return contract, str(canonical or requested_code).strip().upper(), contract_api

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
    return contract, canonical, api


def _query_history_1m(
    api: Any,
    contract: Any,
    *,
    window: _SessionWindow,
    now_ms: int,
) -> tuple[list[dict[str, Any]], int]:
    """依 Shioaji 每次最多 30 日限制分段查詢，再用 timestamp 去重合併。"""
    start_day = datetime.fromtimestamp(window.start_ms / 1000, TW_TZ).date()
    end_day = datetime.fromtimestamp(min(now_ms, window.end_ms) / 1000, TW_TZ).date()
    cursor = start_day
    rows: dict[int, dict[str, Any]] = {}
    request_count = 0

    while cursor <= end_day:
        chunk_end = min(cursor + timedelta(days=MAX_KBARS_CALENDAR_DAYS - 1), end_day)
        kbars = api.kbars(contract=contract, start=cursor.isoformat(), end=chunk_end.isoformat())
        request_count += 1
        for bar in _normalize_1m(kbars, window=window, now_ms=now_ms):
            rows[int(bar["ts"])] = bar
        cursor = chunk_end + timedelta(days=1)

    return [rows[ts] for ts in sorted(rows)], request_count


def _bootstrap_history(
    requested_code: str,
    window: _SessionWindow,
    *,
    service: Any,
    now_ms: int,
    monotonic_fn: Callable[[], float],
    stock_futures_lookup: Optional[Callable[[str], Optional[tuple[Any, str]]]] = None,
    stock_futures_history_apis: Optional[Callable[[], Iterable[Any]]] = None,
) -> _HistoryEntry:
    contract, canonical, api = _resolve_contract(
        service,
        requested_code,
        stock_futures_lookup=stock_futures_lookup,
    )
    primary_api = getattr(service, "api", None)
    display_name = str(getattr(contract, "name", "") or requested_code).strip()
    logged_in = bool(
        api is not None
        and (
            api is not primary_api
            or getattr(getattr(service, "state", None), "logged_in", False)
        )
    )
    if api is None or not logged_in or contract is None:
        error = "Shioaji 尚未登入" if api is None or not logged_in else f"找不到期貨合約：{requested_code}"
        return _HistoryEntry(window.start_ms, canonical, [], [], monotonic_fn(), False, error, display_name)

    try:
        # 訂閱所在 pool 與主 QuoteService 是不同 Shioaji instance。合約物件優先
        # 在實際查詢的 API 上重取；跨 instance 沿用 contract 可能只回當日或 NotReady。
        bars_1m: list[dict[str, Any]] = []
        request_count = 0
        attempt_errors: list[str] = []
        attempted_api_ids: set[int] = set()

        def try_history(candidate_api: Any, label: str, *, allow_original_contract: bool = False) -> None:
            nonlocal bars_1m, request_count
            if candidate_api is None or id(candidate_api) in attempted_api_ids:
                return
            attempted_api_ids.add(id(candidate_api))
            candidate_contract = _lookup_api_contract_candidates(
                candidate_api,
                canonical,
                getattr(contract, "target_code", None),
                requested_code,
                getattr(contract, "code", None),
            )
            if candidate_contract is None and allow_original_contract:
                candidate_contract = contract
            if candidate_contract is None:
                attempt_errors.append(f"{label}: 找不到自己的期貨合約 {canonical}")
                return
            try:
                candidate_bars, candidate_requests = _query_history_1m(
                    candidate_api,
                    candidate_contract,
                    window=window,
                    now_ms=now_ms,
                )
                if len(candidate_bars) > len(bars_1m):
                    bars_1m = candidate_bars
                    request_count = candidate_requests
            except Exception as exc:
                attempt_errors.append(f"{label}: {exc}")

        try_history(api, "assigned pool", allow_original_contract=True)

        # 冷啟動時共享訂閱 pool 的行情推播已可用，但歷史 P2P Session 可能仍
        # NotReady。此時改用已登入的主 API 重試；若兩邊都有資料，保留較完整者。
        history_day_count = len({
            datetime.fromtimestamp(int(bar["ts"]) / 1000, TW_TZ).date()
            for bar in bars_1m
        })
        primary_logged_in = bool(
            primary_api is not None and getattr(getattr(service, "state", None), "logged_in", False)
        )
        should_try_primary = (
            primary_logged_in
            and primary_api is not api
            and (attempt_errors or history_day_count < 2)
        )
        if should_try_primary:
            # Shioaji Contract 是查詢參數資料模型，不綁定行情 Session；主 API
            # catalog 尚未載入該股期時，仍可序列化已解析的近月 contract 查 Kbars。
            try_history(primary_api, "primary API", allow_original_contract=True)

        # 同一帳號的多條 Shioaji 連線，行情 Session 與歷史 P2P Session 的
        # 就緒時間可能不同。若指定 pool／主 API 都沒補到至少兩天，依序嘗試
        # 其餘已登入的共享 API；每條連線都只使用自己 catalog 的合約物件。
        history_day_count = len({
            datetime.fromtimestamp(int(bar["ts"]) / 1000, TW_TZ).date()
            for bar in bars_1m
        })
        if not _has_night_session(requested_code) and history_day_count < 2:
            history_api_factory = stock_futures_history_apis or _default_stock_futures_history_apis
            try:
                other_apis = list(history_api_factory() or [])
            except Exception as exc:
                other_apis = []
                attempt_errors.append(f"shared pools: {exc}")
            for index, candidate_api in enumerate(other_apis):
                try_history(candidate_api, f"shared pool #{index}", allow_original_contract=True)
                history_day_count = len({
                    datetime.fromtimestamp(int(bar["ts"]) / 1000, TW_TZ).date()
                    for bar in bars_1m
                })
                if history_day_count >= 2:
                    break

        if not bars_1m and attempt_errors:
            raise RuntimeError("; ".join(attempt_errors))
        bars_5m = _aggregate_5m(bars_1m, now_ms=now_ms)
        ok = bool(bars_1m)
        logger.info(
            "[Futures KBar] %s 補齊完成: 1m=%d, 5m=%d, requests=%d, session=%s",
            canonical,
            len(bars_1m),
            len(bars_5m),
            request_count,
            window.name,
        )
        return _HistoryEntry(
            window.start_ms,
            canonical,
            bars_1m,
            bars_5m,
            monotonic_fn(),
            ok,
            None if ok else "Shioaji 本交易時段 Kbars 暫無資料",
            display_name,
        )
    except Exception as exc:
        logger.warning("[Futures KBar] %s 歷史補齊失敗: %s", requested_code, exc)
        return _HistoryEntry(window.start_ms, canonical, [], [], monotonic_fn(), False, str(exc), display_name)


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
    stock_futures_history_apis: Optional[Callable[[], Iterable[Any]]] = None,
    history_days: Optional[int] = None,
) -> dict[str, Any]:
    if interval not in {"1m", "5m", "1d"}:
        raise ValueError(f"不支援 interval: {interval}")
    requested_code = str(futures_code).strip().upper()
    now_value = now_ms if now_ms is not None else int(datetime.now(TW_TZ).timestamp() * 1000)
    current_window = _session_window(now_value, requested_code)
    default_days = 180 if interval == "1d" else 7
    lookback_days = max(1, min(370, int(history_days or default_days)))
    history_window = _SessionWindow(
        current_window.name,
        current_window.start_ms - (lookback_days - 1) * 24 * 60 * 60 * 1000,
        current_window.end_ms,
    )
    service = service if service is not None else _default_service()
    hub = hub if hub is not None else _default_hub()
    key = (requested_code, current_window.start_ms, lookback_days)

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
                    history_window,
                    service=service,
                    now_ms=now_value,
                    monotonic_fn=monotonic_fn,
                    stock_futures_lookup=stock_futures_lookup,
                    stock_futures_history_apis=stock_futures_history_apis,
                )
                with _cache_lock:
                    _history_cache[key] = entry

    if interval == "1d":
        live = list(hub.get_live_futures_bars_1m(entry.code) or [])
        if entry.code != requested_code and not live:
            live = list(hub.get_live_futures_bars_1m(requested_code) or [])
        bars = _aggregate_1d(_merge(entry.bars_1m, live, history_window))
    else:
        live_getter = hub.get_live_futures_bars_1m if interval == "1m" else hub.get_live_futures_bars
        live = list(live_getter(entry.code) or [])
        if entry.code != requested_code and not live:
            live = list(live_getter(requested_code) or [])
        history = entry.bars_1m if interval == "1m" else entry.bars_5m
        bars = _merge(history, live, history_window)

    return {
        "status": "ok",
        "requested_code": requested_code,
        "code": entry.code,
        "name": entry.name or requested_code,
        "interval": interval,
        "session": current_window.name,
        "history_days": lookback_days,
        "bar_count": len(bars),
        "bars": bars,
        "bootstrap": {
            "session_start": datetime.fromtimestamp(history_window.start_ms / 1000, TW_TZ).isoformat(),
            "session_end": datetime.fromtimestamp(history_window.end_ms / 1000, TW_TZ).isoformat(),
            "history_1m": len(entry.bars_1m),
            "history_5m": len(entry.bars_5m),
            "live_count": len(live),
            "history_ok": entry.ok,
            "error": entry.error,
            "source": "shioaji_futures_kbars+realtime_hub",
        },
    }
