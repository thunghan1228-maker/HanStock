"""HanStock 盤中策略專用的 Shioaji 多日 5 分 K 歷史服務。

用途：把網站盤中訊號所需的 MA20／昨日高低／昨收脈絡也統一搬回 HanStock Hub，
不再依賴 Yahoo 歷史 5 分 K。正式觸發仍由 MarketDataHub 即時資料接續。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from otc_index import TW_TZ, aggregate_1m_to_5m, normalize_kbars_1m, taipei_minute_of_day, taipei_trade_date
from stock_bar_bootstrap import _default_hub, _default_service, _history_slots, _resolve_stock_contract
from history_cache import HistoryCache
from history_quota import history_quota
from history_sources import fetch_minute_bars_chain, stock_market

logger = logging.getLogger("hanstock.stock_history_service")

DEFAULT_CALENDAR_DAYS = 14
MAX_HISTORY_5M = 300  # 5分K每個交易日約54根（09:00-13:30），300根約可涵蓋5個交易日
MAX_HISTORY_1M = 1500  # 1分K每個交易日約270根，1500根約可涵蓋5個交易日
RETRY_AFTER_SECONDS = 30.0
# 永豐一天 500MB 歷史流量：開圖、今日缺口這些「即時」需求剩不到這麼多時就改走 FinMind／Yahoo，
# 把額度留給收盤後訊號校正（priority="backfill"，全部股票都要今天的 K 棒）。2026-09-23 額度在
# 13:35 前就被用光，校正抓不到今天的 K 棒，整天 12空／創高黑龍都是 0。
INTERACTIVE_RESERVE_BYTES = max(0, int(float(os.getenv("HANSTOCK_HISTORY_INTERACTIVE_RESERVE_MB", "100")) * 1_000_000))
PRIORITY_INTERACTIVE = "interactive"
PRIORITY_BACKFILL = "backfill"


def _quota_error(api: Any, priority: str) -> Optional[str]:
    if priority == PRIORITY_BACKFILL:
        return history_quota.check(api)
    return history_quota.check_background(api, reserve_bytes=INTERACTIVE_RESERVE_BYTES)


@dataclass
class _History5mEntry:
    trade_date: str
    start_date: str
    bars_5m: list[dict[str, Any]]
    bars_1m: list[dict[str, Any]]
    fetched_at_monotonic: float
    ok: bool
    error: Optional[str] = None
    source: str = "shioaji"
    settled: bool = False  # 收盤後（13:35+）抓的，已含「今天」；盤中抓的收盤後要重抓一次


# 永豐拿不到、備援也都失敗時，這檔 5 分鐘內不再重打 FinMind/Yahoo（前端每 15 秒輪詢、
# 失敗快取只有 30 秒，不擋的話會一直打外部 API）。
FALLBACK_RETRY_SECONDS = 300.0

_lock = threading.RLock()
_code_locks: dict[str, threading.Lock] = {}
_cache: dict[str, _History5mEntry] = HistoryCache(max_entries=512, max_bars=72_000)
_fallback_failed_at: dict[str, float] = {}


def clear_stock_history_cache() -> None:
    with _lock:
        _cache.clear()
        _code_locks.clear()
        _fallback_failed_at.clear()
        _gap_cache.clear()
        _gap_fallback_failed_at.clear()


def _code_lock(code: str) -> threading.Lock:
    with _lock:
        lock = _code_locks.get(code)
        if lock is None:
            lock = threading.Lock()
            _code_locks[code] = lock
        return lock


def _is_settled(now_ms: int) -> bool:
    """13:35 收盤後 kbars 對「今天」已經穩定；跟 kline_signal_backfill_collector 的門檻一致。"""
    now_local = datetime.fromtimestamp(now_ms / 1000, TW_TZ)
    return (now_local.hour, now_local.minute) >= (13, 35)


def _cached(code: str, trade_date: str, start_date: str, now_mono: float, now_ms: Optional[int] = None) -> Optional[_History5mEntry]:
    with _lock:
        entry = _cache.get(code)
    if entry is None or entry.trade_date != trade_date:
        return None
    # 已快取的起日更早（或相同）就足以滿足本次需求。
    if entry.start_date > start_date:
        return None
    # 盤中抓的不含「今天」；收盤後要重抓一次把今天含進來，不然 2408 這種盤中開過圖的股票，
    # 收盤後整天的 K 棒都不見（Hub 又剛好重啟過就什麼都沒有）。
    if now_ms is not None and _is_settled(now_ms) and not entry.settled:
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
    priority: str = PRIORITY_INTERACTIVE,
) -> _History5mEntry:
    # Share the broker budget with same-day backfill. Requests must not queue
    # behind slow SDK calls while current Hub bars are already available.
    if not _history_slots.acquire(blocking=False):
        return _deferred_history(code, trade_date, start_date, monotonic_fn())
    try:
        quota_error = _quota_error(getattr(service, "api", None), priority)
        if quota_error:
            fallback = _fallback_history(
                code, trade_date, start_date, now_ms=now_ms, monotonic_fn=monotonic_fn, reason=quota_error,
            )
            if fallback is not None:
                return fallback
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

    def failed(error: str) -> _History5mEntry:
        fallback = _fallback_history(code, trade_date, start_date, now_ms=now_ms, monotonic_fn=monotonic_fn, reason=error)
        if fallback is not None:
            return fallback
        return _store(code, _History5mEntry(
            trade_date=trade_date, start_date=start_date, bars_5m=[], bars_1m=[],
            fetched_at_monotonic=monotonic_fn(), ok=False, error=error,
        ))

    if api is None or not logged_in:
        return failed("Shioaji 尚未登入")
    contract = _resolve_stock_contract(service, code)
    if contract is None:
        return failed(f"找不到股票合約：{code}")

    try:
        # 一檔股票一天只做一次多日 kbars 查詢；normalize 不指定 trade_date，保留整段正式盤資料。
        kbars = api.kbars(contract=contract, start=start_date, end=trade_date)
        bars_1m = normalize_kbars_1m(kbars, trade_date=None, include_current=False, now_ms=now_ms)
        bars_5m = aggregate_1m_to_5m(bars_1m, include_current=False, now_ms=now_ms)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Stock History5m] %s 多日 Kbars 失敗: %s", code, exc)
        return failed(str(exc))
    bars_5m, bars_1m = _trim_to_settled_window(bars_5m, bars_1m, trade_date, start_date, now_ms)
    if not bars_5m:
        return failed("Shioaji 多日 Kbars 暫無資料")
    entry = _store(code, _History5mEntry(
        trade_date=trade_date, start_date=start_date, bars_5m=bars_5m, bars_1m=bars_1m,
        fetched_at_monotonic=monotonic_fn(), ok=True, error=None, settled=_is_settled(now_ms),
    ))
    logger.info("[Stock History5m] %s 多日補齊: start=%s end=%s bars=%d", code, start_date, trade_date, len(bars_5m))
    return entry


def _trim_to_settled_window(
    bars_5m: list[dict[str, Any]],
    bars_1m: list[dict[str, Any]],
    trade_date: str,
    start_date: str,
    now_ms: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """今天(trade_date)盤中查詢kbars常常還沒到齊(有落後)，早上剛開盤第一次查詢抓到
    的今天前幾根若被快取一整天，靠後面merge時的即時Hub資料永遠補不回來，變成固定
    在某個交易日開盤附近少了好幾根K棒。但如果現在已經收盤(13:35後，留5分鐘緩衝)，
    歷史對「今天」應該已經穩定不會再變，這時候放心含進來一起快取，否則收盤後才
    第一次查看、當天完全沒被即時追蹤過的股票(Hub也沒有資料)會整天完全看不到今天
    的K棒。收盤前一律不含「今天」，交給即時Hub負責(get_stock_history_bars_5m/1m
    每次都會重新讀Hub，不會有這個快取過期問題)。"""
    today_settled = _is_settled(now_ms)
    upper_date = trade_date if today_settled else (
        datetime.strptime(trade_date, "%Y-%m-%d").date() - timedelta(days=1)
    ).isoformat()
    bars_5m = [bar for bar in bars_5m if start_date <= taipei_trade_date(int(bar["ts"])) <= upper_date][-MAX_HISTORY_5M:]
    bars_1m = [bar for bar in bars_1m if start_date <= taipei_trade_date(int(bar["ts"])) <= upper_date][-MAX_HISTORY_1M:]
    return bars_5m, bars_1m


def _fallback_history(
    code: str,
    trade_date: str,
    start_date: str,
    *,
    now_ms: int,
    monotonic_fn: Callable[[], float],
    reason: str,
) -> Optional[_History5mEntry]:
    """永豐拿不到（額度用完、沒登入、沒合約、查詢失敗、沒資料）時改走 FinMind → Yahoo。
    備援也都失敗的話這檔 FALLBACK_RETRY_SECONDS 內不再重試。"""
    now = monotonic_fn()
    with _lock:
        failed_at = _fallback_failed_at.get(code)
    if failed_at is not None and now - failed_at < FALLBACK_RETRY_SECONDS:
        return None
    try:
        bars_1m, source = fetch_minute_bars_chain(code, start_date, trade_date, market=stock_market(code))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Stock History5m] %s 備援來源失敗: %s", code, exc)
        bars_1m, source = [], None
    current_minute_start = now_ms - (now_ms % 60_000)
    bars_1m = [bar for bar in bars_1m if int(bar["ts"]) < current_minute_start]
    bars_5m = aggregate_1m_to_5m(bars_1m, include_current=False, now_ms=now_ms)
    bars_5m, bars_1m = _trim_to_settled_window(bars_5m, bars_1m, trade_date, start_date, now_ms)
    if source is None or not bars_5m:
        with _lock:
            _fallback_failed_at[code] = now
        return None
    with _lock:
        _fallback_failed_at.pop(code, None)
    logger.info("[Stock History5m] %s 永豐不可用（%s），改用 %s 補齊: bars=%d", code, reason, source, len(bars_5m))
    return _store(code, _History5mEntry(
        trade_date=trade_date, start_date=start_date, bars_5m=bars_5m, bars_1m=bars_1m,
        fetched_at_monotonic=monotonic_fn(), ok=True, error=None, source=source, settled=_is_settled(now_ms),
    ))


# ---------------------------------------------------------------------------
# 今天開盤到 Hub 第一根之前的缺口（Railway 中途重啟／部署後最常見）
# ---------------------------------------------------------------------------
# 收盤前多日歷史刻意不含「今天」（見 _trim_to_settled_window），今天整段交給即時 Hub；但 Hub 只活在
# 記憶體裡，Railway 中途重啟（例如部署）之後只從重啟那一刻開始累積，開盤到重啟之間那幾個小時的
# K 棒就整段消失，要到收盤後才會從多日歷史補回來（2026-09-23 11:25 部署後，8054 的 5 分 K 只剩
# 11:30 以後）。這裡在 Hub 今天的第一根不是 09:00 那根時，用 Shioaji 當日 kbars 只補「開盤到 Hub
# 第一根之前」這段；Hub 有的部分永遠以 Hub 為準。補到的結果快取到收盤，補不齊（盤中 kbars 常落後）
# 才隔一段時間再抓一次。
GAP_RETRY_SECONDS = 120.0
GAP_MIN_MINUTES_AFTER_OPEN = 5
FIVE_MIN_MS = 5 * 60_000


@dataclass
class _TodayGapEntry:
    trade_date: str
    bars_1m: list[dict[str, Any]]
    bars_5m: list[dict[str, Any]]
    covers_until_ms: Optional[int]  # 這次抓到的最後一根 5 分 K（bar-start）
    fetched_at_monotonic: float
    ok: bool
    error: Optional[str] = None
    source: str = "shioaji"


_gap_cache: dict[str, _TodayGapEntry] = {}
_gap_fallback_failed_at: dict[str, float] = {}  # 跟多日歷史的備援冷卻分開：多日抓不到不代表今天也抓不到


def _session_open_ms(trade_date: str) -> int:
    return int(datetime.strptime(trade_date, "%Y-%m-%d").replace(hour=9, minute=0, tzinfo=TW_TZ).timestamp() * 1000)


def _first_ts_on(bars: list[dict[str, Any]], trade_date: str) -> Optional[int]:
    """這批 bars 裡屬於 trade_date 的最早一根 bar-start ts；沒有就 None。"""
    first: Optional[int] = None
    for raw in bars:
        try:
            ts = int(raw["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if taipei_trade_date(ts) != trade_date:
            continue
        if first is None or ts < first:
            first = ts
    return first


def _store_gap(code: str, entry: _TodayGapEntry) -> _TodayGapEntry:
    with _lock:
        _gap_cache[code] = entry
    return entry


def _today_gap_bars(
    code: str,
    trade_date: str,
    live_first_ts: Optional[int],
    *,
    service: Any,
    now_ms: int,
    monotonic_fn: Callable[[], float],
    fetch: bool = True,
    priority: str = PRIORITY_INTERACTIVE,
) -> Optional[_TodayGapEntry]:
    """live_first_ts＝Hub 今天第一根的 bar-start ts；None 代表 Hub 今天完全沒有這檔的資料。
    fetch=False（同一檔的多日歷史正被另一個請求抓著）時只回已快取的，不再多打一次 kbars。
    回 None 代表不需要補：還沒開盤／已收盤（多日歷史自己就含今天）／週末／Hub 從開盤就有。"""
    now_dt = datetime.fromtimestamp(now_ms / 1000, TW_TZ)
    if now_dt.strftime("%Y-%m-%d") != trade_date or now_dt.weekday() >= 5:
        return None
    open_ms = _session_open_ms(trade_date)
    if now_ms < open_ms + GAP_MIN_MINUTES_AFTER_OPEN * 60_000:
        return None
    if (now_dt.hour, now_dt.minute) >= (13, 35):
        return None
    if live_first_ts is not None and live_first_ts <= open_ms:
        return None
    need_until = live_first_ts if live_first_ts is not None else now_ms
    now = monotonic_fn()
    with _lock:
        entry = _gap_cache.get(code)
    if entry is not None and entry.trade_date == trade_date:
        complete = entry.ok and entry.covers_until_ms is not None and entry.covers_until_ms >= need_until - FIVE_MIN_MS
        if complete or now - entry.fetched_at_monotonic < GAP_RETRY_SECONDS:
            return entry
    else:
        entry = None
    if not fetch or not _history_slots.acquire(blocking=False):
        return entry
    try:
        return _fetch_today_gap_once(
            code, trade_date, service=service, now_ms=now_ms, monotonic_fn=monotonic_fn, previous=entry, priority=priority,
        )
    finally:
        _history_slots.release()


def _fetch_today_gap_once(
    code: str,
    trade_date: str,
    *,
    service: Any,
    now_ms: int,
    monotonic_fn: Callable[[], float],
    previous: Optional[_TodayGapEntry],
    priority: str = PRIORITY_INTERACTIVE,
) -> _TodayGapEntry:
    def failed(error: str) -> _TodayGapEntry:
        fallback = _fallback_today_gap(code, trade_date, now_ms=now_ms, monotonic_fn=monotonic_fn, reason=error)
        if fallback is not None:
            return fallback
        # 抓不到就先留著上一次補到的，不要把已經補回來的又清掉。
        return _store_gap(code, _TodayGapEntry(
            trade_date=trade_date,
            bars_1m=previous.bars_1m if previous is not None else [],
            bars_5m=previous.bars_5m if previous is not None else [],
            covers_until_ms=previous.covers_until_ms if previous is not None else None,
            fetched_at_monotonic=monotonic_fn(), ok=False, error=error,
            source=previous.source if previous is not None else "shioaji",
        ))

    api = getattr(service, "api", None)
    logged_in = bool(getattr(getattr(service, "state", None), "logged_in", False))
    if api is None or not logged_in:
        return failed("Shioaji 尚未登入")
    quota_error = _quota_error(api, priority)
    if quota_error:
        return failed(quota_error)
    contract = _resolve_stock_contract(service, code)
    if contract is None:
        return failed(f"找不到股票合約：{code}")
    try:
        kbars = api.kbars(contract=contract, start=trade_date, end=trade_date)
        bars_1m = normalize_kbars_1m(kbars, trade_date=trade_date, include_current=False, now_ms=now_ms)
        bars_5m = aggregate_1m_to_5m(bars_1m, include_current=False, now_ms=now_ms)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Stock History5m] %s 今日缺口 kbars 失敗: %s", code, exc)
        return failed(str(exc))
    covers = max((int(bar["ts"]) for bar in bars_5m), default=None)
    entry = _store_gap(code, _TodayGapEntry(
        trade_date=trade_date, bars_1m=bars_1m, bars_5m=bars_5m, covers_until_ms=covers,
        fetched_at_monotonic=monotonic_fn(), ok=True, error=None,
    ))
    logger.info("[Stock History5m] %s 今日缺口補齊: bars_5m=%d", code, len(bars_5m))
    return entry


def _fallback_today_gap(
    code: str, trade_date: str, *, now_ms: int, monotonic_fn: Callable[[], float], reason: str,
) -> Optional[_TodayGapEntry]:
    """永豐拿不到今天的 kbars 時改走 FinMind → Yahoo（跟多日歷史同一條備援鏈，各自的失敗冷卻）。"""
    now = monotonic_fn()
    with _lock:
        failed_at = _gap_fallback_failed_at.get(code)
    if failed_at is not None and now - failed_at < FALLBACK_RETRY_SECONDS:
        return None
    try:
        bars_1m, source = fetch_minute_bars_chain(code, trade_date, trade_date, market=stock_market(code))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Stock History5m] %s 今日缺口備援來源失敗: %s", code, exc)
        bars_1m, source = [], None
    current_minute_start = now_ms - (now_ms % 60_000)
    bars_1m = [bar for bar in bars_1m if int(bar["ts"]) < current_minute_start and taipei_trade_date(int(bar["ts"])) == trade_date]
    bars_5m = aggregate_1m_to_5m(bars_1m, include_current=False, now_ms=now_ms)
    if source is None or not bars_5m:
        with _lock:
            _gap_fallback_failed_at[code] = now
        return None
    with _lock:
        _gap_fallback_failed_at.pop(code, None)
    logger.info("[Stock History5m] %s 今日缺口永豐不可用（%s），改用 %s 補齊: bars=%d", code, reason, source, len(bars_5m))
    return _store_gap(code, _TodayGapEntry(
        trade_date=trade_date, bars_1m=bars_1m, bars_5m=bars_5m,
        covers_until_ms=max((int(bar["ts"]) for bar in bars_5m), default=None),
        fetched_at_monotonic=monotonic_fn(), ok=True, error=None, source=source,
    ))


def _gap_info(gap: Optional[_TodayGapEntry], filled: int, live_first_ts: Optional[int]) -> Optional[dict[str, Any]]:
    if gap is None:
        return None
    return {
        "filled": filled, "ok": gap.ok, "error": gap.error, "source": gap.source,
        "covers_until": gap.covers_until_ms, "live_first": live_first_ts,
    }


def get_stock_history_bars_5m(
    stock_code: str,
    *,
    calendar_days: int = DEFAULT_CALENDAR_DAYS,
    service: Any = None,
    hub: Any = None,
    now_ms: Optional[int] = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
    priority: str = PRIORITY_INTERACTIVE,
) -> dict[str, Any]:
    """取得策略用多日 5 分 K；歷史由 Shioaji kbars，今天即時由 Hub 覆蓋。
    priority="backfill"（收盤後訊號校正）可以用到永豐留給它的保留額度，其他呼叫剩不到保留額度就走備援。"""
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

    entry = _cached(code, trade_date, start_date, monotonic_fn(), now_ms=now_value)
    fetch_gap = True
    if entry is None:
        code_lock = _code_lock(code)
        if not code_lock.acquire(blocking=False):
            entry = _deferred_history(code, trade_date, start_date, monotonic_fn())
            fetch_gap = False  # 這檔正在抓歷史，這次不再多打一次 kbars
        else:
            try:
                entry = _cached(code, trade_date, start_date, monotonic_fn(), now_ms=now_value)
                if entry is None:
                    entry = _fetch_history(
                        code,
                        trade_date,
                        start_date,
                        service=service,
                        now_ms=now_value,
                        monotonic_fn=monotonic_fn,
                        priority=priority,
                    )
            finally:
                code_lock.release()

    # 歷史先放、今天開盤到 Hub 第一根之前的缺口次之、即時最後；同 timestamp 由即時 Hub 覆蓋。
    live = list(hub.get_live_bars(code) or [])
    live_first = _first_ts_on(live, trade_date)
    gap = _today_gap_bars(code, trade_date, live_first, service=service, now_ms=now_value, monotonic_fn=monotonic_fn, fetch=fetch_gap, priority=priority)
    gap_bars = [bar for bar in gap.bars_5m if live_first is None or int(bar["ts"]) < live_first] if gap is not None else []
    merged: dict[int, dict[str, Any]] = {}
    for source in (entry.bars_5m, gap_bars, live):
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
            "history_source": entry.source,
            "error": entry.error,
            "today_gap": _gap_info(gap, len(gap_bars), live_first),
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
    priority: str = PRIORITY_INTERACTIVE,
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

    entry = _cached(code, trade_date, start_date, monotonic_fn(), now_ms=now_value)
    fetch_gap = True
    if entry is None:
        code_lock = _code_lock(code)
        if not code_lock.acquire(blocking=False):
            entry = _deferred_history(code, trade_date, start_date, monotonic_fn())
            fetch_gap = False  # 這檔正在抓歷史，這次不再多打一次 kbars
        else:
            try:
                entry = _cached(code, trade_date, start_date, monotonic_fn(), now_ms=now_value)
                if entry is None:
                    entry = _fetch_history(
                        code,
                        trade_date,
                        start_date,
                        service=service,
                        now_ms=now_value,
                        monotonic_fn=monotonic_fn,
                        priority=priority,
                    )
            finally:
                code_lock.release()

    live = list(hub.get_live_bars_1m(code) or [])
    live_first = _first_ts_on(live, trade_date)
    gap = _today_gap_bars(code, trade_date, live_first, service=service, now_ms=now_value, monotonic_fn=monotonic_fn, fetch=fetch_gap, priority=priority)
    gap_bars = [bar for bar in gap.bars_1m if live_first is None or int(bar["ts"]) < live_first] if gap is not None else []
    merged: dict[int, dict[str, Any]] = {}
    for source in (entry.bars_1m, gap_bars, live):
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
            "history_source": entry.source,
            "error": entry.error,
            "today_gap": _gap_info(gap, len(gap_bars), live_first),
            "subscription": subscription,
            "source": "shioaji_kbars_range+realtime_hub",
            "max_history_1m": MAX_HISTORY_1M,
        },
    }
