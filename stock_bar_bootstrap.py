"""HanStock 個股 K 線重啟復原層。

目的：MarketDataHub 的 1m/5m K 棒原本只存在記憶體；Railway 重啟後會歸零。
本模組在個股 K 線被讀取時：
1. 自動確保該股票 Tick 已訂閱；
2. 以 Shioaji api.kbars() 補齊今日已完成的正式 1 分 K；
3. 聚合成今日已完成的 5 分 K；
4. 與 MarketDataHub 的即時 K 棒依 timestamp 合併（live 覆蓋 history）。

這層不修改 QuoteService / MarketDataHub 的穩定即時流程，也不持久化憑證或行情。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from otc_index import (
    TW_TZ,
    aggregate_1m_to_5m,
    normalize_kbars_1m,
    taipei_minute_of_day,
    taipei_trade_date,
)

logger = logging.getLogger("hanstock.stock_bar_bootstrap")

RETRY_AFTER_SECONDS = 30.0


@dataclass
class _HistoryEntry:
    trade_date: str
    bars_1m: list[dict[str, Any]]
    bars_5m: list[dict[str, Any]]
    fetched_at_monotonic: float
    ok: bool
    error: Optional[str] = None


_cache_lock = threading.RLock()
_history_cache: dict[str, _HistoryEntry] = {}
_code_locks: dict[str, threading.Lock] = {}


def clear_stock_bar_bootstrap_cache() -> None:
    """清空歷史 K 棒快取；主要供測試與日後維運使用。"""
    with _cache_lock:
        _history_cache.clear()
        _code_locks.clear()


def _get_code_lock(code: str) -> threading.Lock:
    with _cache_lock:
        lock = _code_locks.get(code)
        if lock is None:
            lock = threading.Lock()
            _code_locks[code] = lock
        return lock


def _default_service() -> Any:
    from quote_service import get_quote_service

    return get_quote_service()


def _default_hub() -> Any:
    from market_data_hub import get_market_data_hub

    return get_market_data_hub()


def _resolve_stock_contract(service: Any, code: str) -> Any:
    """沿用 QuoteService 的股票合約解析；測試 fake service 也可覆寫。"""
    resolver = getattr(service, "_resolve_stock_contract", None)
    if callable(resolver):
        return resolver(code)

    api = getattr(service, "api", None)
    if api is None:
        return None
    contract = api.contracts.get(code)
    if contract is None:
        try:
            contract = api.Contracts.Stocks[code]
        except Exception:
            contract = None
    return contract


def _safe_bar(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    try:
        ts = int(raw.get("ts"))
        open_ = float(raw.get("open"))
        high = float(raw.get("high"))
        low = float(raw.get("low"))
        close = float(raw.get("close"))
        volume = max(0, int(raw.get("volume", 0) or 0))
        tick_count = max(0, int(raw.get("tick_count", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        return None
    if ts <= 0 or min(open_, high, low, close) <= 0:
        return None
    return {
        "ts": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "tick_count": tick_count,
    }


def _is_formal_stock_bar(ts_ms: int, interval: str, trade_date: str) -> bool:
    if taipei_trade_date(ts_ms) != trade_date:
        return False
    minute = taipei_minute_of_day(ts_ms)
    if interval == "1m":
        # 收盤撮合可能形成 13:30 起始標記；沿用既有 V7 1m 顯示行為。
        return 9 * 60 <= minute <= 13 * 60 + 30
    # 5m 正式最後一根為 13:25~13:30。
    return 9 * 60 <= minute < 13 * 60 + 30


def _merge_bars(
    history: list[dict[str, Any]],
    live: list[dict[str, Any]],
    *,
    interval: str,
    trade_date: str,
) -> list[dict[str, Any]]:
    """history 先放、live 後放；同 timestamp 以即時資料覆蓋。"""
    merged: dict[int, dict[str, Any]] = {}
    for source in (history, live):
        for raw in source:
            bar = _safe_bar(raw)
            if bar is None or not _is_formal_stock_bar(bar["ts"], interval, trade_date):
                continue
            merged[bar["ts"]] = bar
    return [merged[ts] for ts in sorted(merged)]


def _cached_entry(code: str, trade_date: str, now_monotonic: float) -> Optional[_HistoryEntry]:
    with _cache_lock:
        entry = _history_cache.get(code)
    if entry is None or entry.trade_date != trade_date:
        return None
    if not entry.ok and now_monotonic - entry.fetched_at_monotonic >= RETRY_AFTER_SECONDS:
        return None
    return entry


def _store_entry(code: str, entry: _HistoryEntry) -> _HistoryEntry:
    with _cache_lock:
        _history_cache[code] = entry
    return entry


def _bootstrap_history(
    code: str,
    trade_date: str,
    *,
    service: Any,
    now_ms: int,
    monotonic_fn: Callable[[], float],
) -> _HistoryEntry:
    api = getattr(service, "api", None)
    logged_in = bool(getattr(getattr(service, "state", None), "logged_in", False))
    if api is None or not logged_in:
        return _store_entry(
            code,
            _HistoryEntry(
                trade_date=trade_date,
                bars_1m=[],
                bars_5m=[],
                fetched_at_monotonic=monotonic_fn(),
                ok=False,
                error="Shioaji 尚未登入",
            ),
        )

    contract = _resolve_stock_contract(service, code)
    if contract is None:
        return _store_entry(
            code,
            _HistoryEntry(
                trade_date=trade_date,
                bars_1m=[],
                bars_5m=[],
                fetched_at_monotonic=monotonic_fn(),
                ok=False,
                error=f"找不到股票合約：{code}",
            ),
        )

    try:
        kbars = api.kbars(contract=contract, start=trade_date, end=trade_date)
        bars_1m = normalize_kbars_1m(
            kbars,
            trade_date=trade_date,
            include_current=False,
            now_ms=now_ms,
        )
        bars_5m = aggregate_1m_to_5m(
            bars_1m,
            include_current=False,
            now_ms=now_ms,
        )
        ok = bool(bars_1m)
        entry = _HistoryEntry(
            trade_date=trade_date,
            bars_1m=bars_1m,
            bars_5m=bars_5m,
            fetched_at_monotonic=monotonic_fn(),
            ok=ok,
            error=None if ok else "Shioaji 今日 Kbars 暫無資料",
        )
        _store_entry(code, entry)
        logger.info(
            "[Stock KBar] %s 歷史補齊完成: 1m=%d, 5m=%d, date=%s",
            code,
            len(bars_1m),
            len(bars_5m),
            trade_date,
        )
        return entry
    except Exception as exc:
        logger.warning("[Stock KBar] %s 歷史補齊失敗: %s", code, exc)
        return _store_entry(
            code,
            _HistoryEntry(
                trade_date=trade_date,
                bars_1m=[],
                bars_5m=[],
                fetched_at_monotonic=monotonic_fn(),
                ok=False,
                error=str(exc),
            ),
        )


def get_resilient_stock_bars(
    stock_code: str,
    interval: str,
    *,
    service: Any = None,
    hub: Any = None,
    now_ms: Optional[int] = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """取得可跨 Railway 重啟復原的今日個股 1m/5m K 棒。"""
    code = str(stock_code).strip().upper()
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")

    service = service if service is not None else _default_service()
    hub = hub if hub is not None else _default_hub()
    now_value = now_ms if now_ms is not None else int(datetime.now(TW_TZ).timestamp() * 1000)
    trade_date = datetime.fromtimestamp(now_value / 1000, TW_TZ).strftime("%Y-%m-%d")

    subscription: Any = None
    try:
        subscription = service.ensure_stock_subscriptions([code])
    except Exception as exc:
        logger.warning("[Stock KBar] %s 自動訂閱失敗: %s", code, exc)
        subscription = {"requested": [code], "failed": {code: str(exc)}}

    now_monotonic = monotonic_fn()
    entry = _cached_entry(code, trade_date, now_monotonic)
    if entry is None:
        lock = _get_code_lock(code)
        with lock:
            # 1m/5m 可能同時進來；進鎖後再查一次避免雙重 kbars()。
            entry = _cached_entry(code, trade_date, monotonic_fn())
            if entry is None:
                entry = _bootstrap_history(
                    code,
                    trade_date,
                    service=service,
                    now_ms=now_value,
                    monotonic_fn=monotonic_fn,
                )

    if interval == "1m":
        history = entry.bars_1m
        live = list(hub.get_live_bars_1m(code) or [])
    else:
        history = entry.bars_5m
        live = list(hub.get_live_bars(code) or [])

    bars = _merge_bars(history, live, interval=interval, trade_date=trade_date)
    return {
        "status": "ok",
        "code": code,
        "interval": interval,
        "bar_count": len(bars),
        "bars": bars,
        "bootstrap": {
            "trade_date": trade_date,
            "history_1m": len(entry.bars_1m),
            "history_5m": len(entry.bars_5m),
            "live_count": len(live),
            "history_ok": entry.ok,
            "error": entry.error,
            "subscription": subscription,
            "source": "shioaji_kbars+realtime_hub",
        },
    }
