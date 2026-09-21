"""HanStock 盤中策略專用的 Shioaji 多日 5 分 K 歷史服務。

用途：把網站盤中訊號所需的 MA20／昨日高低／昨收脈絡也統一搬回 HanStock Hub，
不再依賴 Yahoo 歷史 5 分 K。正式觸發仍由 MarketDataHub 即時資料接續。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from otc_index import TW_TZ, aggregate_1m_to_5m, normalize_kbars_1m, taipei_minute_of_day, taipei_trade_date
from stock_bar_bootstrap import _default_hub, _default_service, _history_slots, _resolve_stock_contract
from history_cache import HistoryCache
from history_quota import history_quota

logger = logging.getLogger("hanstock.stock_history_service")

DEFAULT_CALENDAR_DAYS = 14
MAX_HISTORY_5M = 300  # 5分K每個交易日約54根（09:00-13:30），300根約可涵蓋5個交易日
MAX_HISTORY_1M = 1500  # 1分K每個交易日約270根，1500根約可涵蓋5個交易日
RETRY_AFTER_SECONDS = 30.0


@dataclass
class _History5mEntry:
    trade_date: str
    start_date: str
    bars_5m: list[dict[str, Any]]
    bars_1m: list[dict[str, Any]]
    fetched_at_monotonic: float
    ok: bool
    error: Optional[str] = None


_lock = threading.RLock()
_code_locks: dict[str, threading.Lock] = {}
_cache: dict[str, _History5mEntry] = HistoryCache(max_entries=512, max_bars=72_000)


def clear_stock_history_cache() -> None:
    with _lock:
        _cache.clear()
        _code_locks.clear()


def _code_lock(code: str) -> threading.Lock:
    with _lock:
        lock = _code_locks.get(code)
        if lock is None:
            lock = threading.Lock()
            _code_locks[code] = lock
        return lock


def _cached(code: str, trade_date: str, start_date: str, now_mono: float) -> Optional[_History5mEntry]:
    with _lock:
        entry = _cache.get(code)
    if entry is None or entry.trade_date != trade_date:
        return None
    # 已快取的起日更早（或相同）就足以滿足本次需求。
    if entry.start_date > start_date:
        return None
    if not entry.ok and now_mono - entry.fetched_at_monotonic >= RETRY_AFTER_SECONDS:
        return None
    return entry


def _store(code: str, entry: _History5mEntry) -> _History5mEntry:
    with _lock:
        previous = _cache.get(code)
        if not entry.ok and previous is not None and previous.trade_date == entry.trade_date:
            entry = replace(entry, bars_5m=previous.bars_5m, bars_1m=previous.bars_1m)
        _cache[code] = entry
    return entry


def _deferred_history(code: str, trade_date: str, start_date: str, now: float) -> _History5mEntry:
    error = "歷史回補處理中；即時行情持續顯示"
    with _lock:
        previous = _cache.get(code)
        if previous is not None and previous.trade_date == trade_date:
            return replace(previous, ok=False, error=error)
    return _History5mEntry(trade_date, start_date, [], [], now, False, error)


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
    minute = taipei_minute_of_day(ts)
    if not (9 * 60 <= minute < 13 * 60 + 30):
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


def _fetch_history(
    code: str,
    trade_date: str,
    start_date: str,
    *,
    service: Any,
    now_ms: int,
    monotonic_fn: Callable[[], float],
) -> _History5mEntry:
    # Share the broker budget with same-day backfill. Requests must not queue
    # behind slow SDK calls while current Hub bars are already available.
    if not _history_slots.acquire(blocking=False):
        return _deferred_history(code, trade_date, start_date, monotonic_fn())
    try:
        quota_error = history_quota.check(getattr(service, "api", None))
        if quota_error:
            return replace(_deferred_history(code, trade_date, start_date, monotonic_fn()), error=quota_error)
        return _fetch_history_once(
            code, trade_date, start_date,
            service=service, now_ms=now_ms, monotonic_fn=monotonic_fn,
        )
    finally:
        _history_slots.release()


def _fetch_history_once(
    code: str,
    trade_date: str,
    start_date: str,
    *,
    service: Any,
    now_ms: int,
    monotonic_fn: Callable[[], float],
) -> _History5mEntry:
    api = getattr(service, "api", None)
    logged_in = bool(getattr(getattr(service, "state", None), "logged_in", False))
    if api is None or not logged_in:
        return _store(code, _History5mEntry(
            trade_date=trade_date,
            start_date=start_date,
            bars_5m=[],
            bars_1m=[],
            fetched_at_monotonic=monotonic_fn(),
            ok=False,
            error="Shioaji 尚未登入",
        ))

    contract = _resolve_stock_contract(service, code)
    if contract is None:
        return _store(code, _History5mEntry(
            trade_date=trade_date,
            start_date=start_date,
            bars_5m=[],
            bars_1m=[],
            fetched_at_monotonic=monotonic_fn(),
            ok=False,
            error=f"找不到股票合約：{code}",
        ))

    try:
        # 一檔股票一天只做一次多日 kbars 查詢；normalize 不指定 trade_date，保留整段正式盤資料。
        kbars = api.kbars(contract=contract, start=start_date, end=trade_date)
        bars_1m = normalize_kbars_1m(
            kbars,
            trade_date=None,
            include_current=False,
            now_ms=now_ms,
        )
        bars_5m = aggregate_1m_to_5m(
            bars_1m,
            include_current=False,
            now_ms=now_ms,
        )
        # 今天(trade_date)盤中查詢kbars常常還沒到齊(有落後)，早上剛開盤
        # 第一次查詢抓到的今天前幾根若被快取一整天，靠後面merge時的即時
        # Hub資料永遠補不回來，變成固定在某個交易日開盤附近少了好幾根
        # K棒。但如果現在已經收盤(13:35後，留5分鐘緩衝)，kbars對「今天」
        # 應該已經穩定不會再變，這時候放心含進來一起快取，否則收盤後才
        # 第一次查看、當天完全沒被即時追蹤過的股票(Hub也沒有資料)會整天
        # 完全看不到今天的K棒。收盤前一律不含「今天」，交給即時Hub負責
        # (get_stock_history_bars_5m/1m每次都會重新讀Hub，不會有這個快取
        # 過期問題)。
        now_local = datetime.fromtimestamp(now_ms / 1000, TW_TZ)
        today_kbars_settled = (now_local.hour, now_local.minute) >= (13, 35)
        kbars_upper_date = trade_date if today_kbars_settled else (
            datetime.strptime(trade_date, "%Y-%m-%d").date() - timedelta(days=1)
        ).isoformat()
        bars_5m = [
            bar for bar in bars_5m
            if start_date <= taipei_trade_date(int(bar["ts"])) <= kbars_upper_date
        ][-MAX_HISTORY_5M:]
        bars_1m = [
            bar for bar in bars_1m
            if start_date <= taipei_trade_date(int(bar["ts"])) <= kbars_upper_date
        ][-MAX_HISTORY_1M:]
        entry = _History5mEntry(
            trade_date=trade_date,
            start_date=start_date,
            bars_5m=bars_5m,
            bars_1m=bars_1m,
            fetched_at_monotonic=monotonic_fn(),
            ok=bool(bars_5m),
            error=None if bars_5m else "Shioaji 多日 Kbars 暫無資料",
        )
        entry = _store(code, entry)
        logger.info(
            "[Stock History5m] %s 多日補齊: start=%s end=%s bars=%d",
            code,
            start_date,
            trade_date,
            len(bars_5m),
        )
        return entry
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Stock History5m] %s 多日 Kbars 失敗: %s", code, exc)
        return _store(code, _History5mEntry(
            trade_date=trade_date,
            start_date=start_date,
            bars_5m=[],
            bars_1m=[],
            fetched_at_monotonic=monotonic_fn(),
            ok=False,
            error=str(exc),
        ))


def get_stock_history_bars_5m(
    stock_code: str,
    *,
    calendar_days: int = DEFAULT_CALENDAR_DAYS,
    service: Any = None,
    hub: Any = None,
    now_ms: Optional[int] = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """取得策略用多日 5 分 K；歷史由 Shioaji kbars，今天即時由 Hub 覆蓋。"""
    code = str(stock_code).strip().upper()
    days = max(3, min(int(calendar_days), 31))
    service = service if service is not None else _default_service()
    hub = hub if hub is not None else _default_hub()
    now_value = now_ms if now_ms is not None else int(datetime.now(TW_TZ).timestamp() * 1000)
    now_dt = datetime.fromtimestamp(now_value / 1000, TW_TZ)
    trade_date = now_dt.strftime("%Y-%m-%d")
    start_date = (now_dt.date() - timedelta(days=days - 1)).isoformat()

    subscription: Any = None
    try:
        subscription = service.ensure_stock_subscriptions([code])
    except Exception as exc:  # noqa: BLE001
        # 歷史 Kbars 仍可能可用；訂閱失敗不阻斷整段歷史。
        subscription = {"requested": [code], "failed": {code: str(exc)}}

    entry = _cached(code, trade_date, start_date, monotonic_fn())
    if entry is None:
        code_lock = _code_lock(code)
        if not code_lock.acquire(blocking=False):
            entry = _deferred_history(code, trade_date, start_date, monotonic_fn())
        else:
            try:
                entry = _cached(code, trade_date, start_date, monotonic_fn())
                if entry is None:
                    entry = _fetch_history(
                        code,
                        trade_date,
                        start_date,
                        service=service,
                        now_ms=now_value,
                        monotonic_fn=monotonic_fn,
                    )
            finally:
                code_lock.release()

    # 歷史先放、即時後放；同 timestamp 由即時 Hub 覆蓋。
    merged: dict[int, dict[str, Any]] = {}
    for source in (entry.bars_5m, list(hub.get_live_bars(code) or [])):
        for raw in source:
            bar = _safe_bar(raw)
            if bar is None:
                continue
            date_text = taipei_trade_date(bar["ts"])
            if not (start_date <= date_text <= trade_date):
                continue
            merged[bar["ts"]] = bar

    bars = [merged[ts] for ts in sorted(merged)][-MAX_HISTORY_5M:]
    return {
        "status": "ok",
        "code": code,
        "interval": "5m",
        "bar_count": len(bars),
        "bars": bars,
        "bootstrap": {
            "trade_date": trade_date,
            "start_date": start_date,
            "history_5m": len(entry.bars_5m),
            "history_ok": entry.ok,
            "error": entry.error,
            "subscription": subscription,
            "source": "shioaji_kbars_range+realtime_hub",
            "max_history_5m": MAX_HISTORY_5M,
        },
    }


def get_stock_history_bars_1m(
    stock_code: str,
    *,
    calendar_days: int = 5,
    service: Any = None,
    hub: Any = None,
    now_ms: Optional[int] = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """取得個股多日1分K；歷史由Shioaji kbars，今天即時由Hub覆蓋。跟5分K共用
    同一份多日kbars抓取快取（_fetch_history*一次抓kbars同時產出1分/5分兩種，
    不會為了1分K多打一次Shioaji API）。"""
    code = str(stock_code).strip().upper()
    days = max(3, min(int(calendar_days), 10))
    service = service if service is not None else _default_service()
    hub = hub if hub is not None else _default_hub()
    now_value = now_ms if now_ms is not None else int(datetime.now(TW_TZ).timestamp() * 1000)
    now_dt = datetime.fromtimestamp(now_value / 1000, TW_TZ)
    trade_date = now_dt.strftime("%Y-%m-%d")
    start_date = (now_dt.date() - timedelta(days=days - 1)).isoformat()

    subscription: Any = None
    try:
        subscription = service.ensure_stock_subscriptions([code])
    except Exception as exc:  # noqa: BLE001
        subscription = {"requested": [code], "failed": {code: str(exc)}}

    entry = _cached(code, trade_date, start_date, monotonic_fn())
    if entry is None:
        code_lock = _code_lock(code)
        if not code_lock.acquire(blocking=False):
            entry = _deferred_history(code, trade_date, start_date, monotonic_fn())
        else:
            try:
                entry = _cached(code, trade_date, start_date, monotonic_fn())
                if entry is None:
                    entry = _fetch_history(
                        code,
                        trade_date,
                        start_date,
                        service=service,
                        now_ms=now_value,
                        monotonic_fn=monotonic_fn,
                    )
            finally:
                code_lock.release()

    merged: dict[int, dict[str, Any]] = {}
    for source in (entry.bars_1m, list(hub.get_live_bars_1m(code) or [])):
        for raw in source:
            bar = _safe_bar(raw)
            if bar is None:
                continue
            date_text = taipei_trade_date(bar["ts"])
            if not (start_date <= date_text <= trade_date):
                continue
            merged[bar["ts"]] = bar

    bars = [merged[ts] for ts in sorted(merged)][-MAX_HISTORY_1M:]
    return {
        "status": "ok",
        "code": code,
        "interval": "1m",
        "bar_count": len(bars),
        "bars": bars,
        "bootstrap": {
            "trade_date": trade_date,
            "start_date": start_date,
            "history_1m": len(entry.bars_1m),
            "history_ok": entry.ok,
            "error": entry.error,
            "subscription": subscription,
            "source": "shioaji_kbars_range+realtime_hub",
            "max_history_1m": MAX_HISTORY_1M,
        },
    }
