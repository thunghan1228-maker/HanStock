"""前後 20 族群即時大單候選訂閱維護器。"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, time as dt_time, timedelta, timezone

from intraday_large_order import get_intraday_large_order_monitor, refresh_intraday_large_order_candidates

logger = logging.getLogger("hanstock.intraday_large_order_collector")
POLL_SECONDS = max(5, int(os.getenv("HANSTOCK_INSTANT_LARGE_REFRESH_SECONDS", "15")))
STALE_SECONDS = max(45, int(os.getenv("HANSTOCK_STOCK_TICK_STALE_SECONDS", "90")))
RECOVERY_COOLDOWN_SECONDS = max(
    120,
    int(os.getenv("HANSTOCK_STOCK_TICK_RECOVERY_COOLDOWN_SECONDS", "180")),
)
TW_TZ = timezone(timedelta(hours=8))
_started = False
_lock = threading.Lock()
_prepared_at: float | None = None
_last_recovery_at = float("-inf")   # 還沒重連過（0.0 的話，剛開機 monotonic 還不到冷卻秒數，第一次重連會被當成冷卻中跳過）


def _market_session_open(now: datetime | None = None) -> bool:
    current = (now or datetime.now(TW_TZ)).astimezone(TW_TZ)
    if current.weekday() >= 5:
        return False
    clock = current.time().replace(tzinfo=None)
    return dt_time(8, 55) <= clock <= dt_time(13, 35)


def _ticks_expected(now: datetime | None = None) -> bool:
    """候選還沒好時的看門狗時段：開盤後 09:02～13:30（避開 09:00 前沒有成交、收盤後不再有 Tick）。"""
    current = (now or datetime.now(TW_TZ)).astimezone(TW_TZ)
    if current.weekday() >= 5:
        return False
    clock = current.time().replace(tzinfo=None)
    return dt_time(9, 2) <= clock <= dt_time(13, 30)


def _recover_if_stock_ticks_stale(service, status: dict) -> dict:
    """盤中候選已就緒卻 90 秒無任何台股 Tick 時，自動重建行情連線。

    2026-11-19：前一晚收盤後重新部署，行情 Session 斷了一整個早上（主連線＋共享池都沒有 Tick，共享池 Session down）。
    候選名單要靠 Tick 算族群排名（要 48 族以上），沒有 Tick 就永遠「waiting_group_snapshot」，看門狗原本只在候選就緒後才檢查，
    變成死結、整個早上都沒重連（12空、905／1+2 突破、大戶力全停）。候選還沒好時，只要有訂閱、開盤後（09:02 起）一筆 Tick 都沒有，也要重連。"""
    global _prepared_at, _last_recovery_at
    monitor = get_intraday_large_order_monitor()
    now_mono = time.monotonic()
    prepared = bool(status.get("prepared"))
    health = service.get_stock_health()
    age = health.get("stock_quote_age_seconds")
    subscribed = int(health.get("active_subscription_count") or 0) > 0
    if not prepared and not (subscribed and _ticks_expected()):
        _prepared_at = None
        monitor.update_status(watchdogState="waiting_candidates")
        return status
    if _prepared_at is None:
        _prepared_at = now_mono

    stale = age is None or float(age) > STALE_SECONDS
    monitor.update_status(
        watchdogState=("stale" if stale else "healthy") if prepared else ("stale_waiting_candidates" if stale else "waiting_candidates"),
        stockQuoteAgeSeconds=age,
        stockQuoteStale=stale,
        stockActiveSubscriptionCount=health.get("active_subscription_count"),
        stockCachedQuoteCount=health.get("cached_quote_count"),
        watchdogThresholdSeconds=STALE_SECONDS,
    )
    if not _market_session_open() or not stale:
        return status
    if now_mono - _prepared_at < STALE_SECONDS:
        return status
    if now_mono - _last_recovery_at < RECOVERY_COOLDOWN_SECONDS:
        return status

    try:
        from stock_futures_service import get_stock_futures_quote_service

        reason = f"台股即時 Tick 已逾時 {age if age is not None else '無資料'} 秒"
        triggered = get_stock_futures_quote_service().trigger_stale_stock_recovery(
            service,
            reason,
        )
        if triggered:
            _last_recovery_at = now_mono
            monitor.update_status(
                watchdogState="recovering",
                recoveryTriggeredAt=datetime.now(TW_TZ).isoformat(),
                recoveryReason=reason,
            )
            logger.warning("盤中即時大單行情斷流，已啟動自動重連：%s", reason)
    except Exception:
        logger.exception("盤中即時大單行情斷流自動恢復失敗")
    return status


def collect_once() -> dict:
    from quote_service import get_quote_service
    service = get_quote_service()
    if not bool(getattr(getattr(service, "state", None), "logged_in", False)):
        return {"prepared": False, "reason": "shioaji_not_ready"}
    status = refresh_intraday_large_order_candidates(service)
    get_intraday_large_order_monitor().flush_pending_signals()
    return _recover_if_stock_ticks_stale(service, status)


def collector_status() -> dict:
    return get_intraday_large_order_monitor().status()


def _loop() -> None:
    while True:
        try:
            collect_once()
        except Exception:  # noqa: BLE001
            logger.exception("前後 20 族群即時大單候選更新失敗")
        time.sleep(POLL_SECONDS)


def start_intraday_large_order_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_INSTANT_LARGE_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return False
        threading.Thread(target=_loop, name="hanstock-instant-large-order", daemon=True).start()
        _started = True
        return True
