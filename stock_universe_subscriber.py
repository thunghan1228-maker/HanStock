"""盤中把 55 個族群的股票全部訂閱永豐逐筆（2026-10-13 使用者）。

原本只有主連線、最多 190 檔，先搶先贏（每個族群 2 檔代表＋早上漲跌最多的族群），一滿就不換，
盤中大戶力、特大買賣單、主力翻多空、四項精選、創高黑龍盤中只看得到這 190 檔，其餘要等收盤後回補。
現在主連線滿了會改訂在共享連線上（quote_service.ensure_stock_subscriptions），這裡每分鐘把族群表
全部股票送一次：已經訂閱的只是確認，連線重建後掉的會重新訂回去。

只在交易日 08:35～13:35 做（盤前試撮也要收）；共享連線失敗（例如登入被限流）時拉長間隔再試，不要每分鐘一直登入。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, time as dt_time, timedelta, timezone

logger = logging.getLogger("hanstock.stock_universe_subscriber")
TW_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = max(30, int(os.getenv("HANSTOCK_STOCK_UNIVERSE_SUBSCRIBE_SECONDS", "60")))
WINDOW_START = dt_time(8, 35)
WINDOW_END = dt_time(13, 35)
MAX_BACKOFF_SECONDS = 15 * 60

_started = False
_lock = threading.Lock()
_status: dict = {"state": "idle"}


def in_subscribe_window(now: datetime | None = None) -> bool:
    current = (now or datetime.now(TW_TZ)).astimezone(TW_TZ)
    if current.weekday() >= 5:
        return False
    try:
        from trading_days import is_trading_day

        if not is_trading_day(current.date()):
            return False
    except Exception:  # noqa: BLE001 — 休市日表讀不到就照平日處理
        pass
    clock = current.time().replace(tzinfo=None)
    return WINDOW_START <= clock <= WINDOW_END


def subscribe_once(service=None) -> dict:
    """把族群表全部股票送去訂閱一次，回傳摘要（新訂、已訂、失敗數）。"""
    if service is None:
        from quote_service import get_quote_service

        service = get_quote_service()
    if not bool(getattr(getattr(service, "state", None), "logged_in", False)):
        return {"skipped": "shioaji_not_ready"}
    from stock_groups import industry_group_codes

    codes = sorted(industry_group_codes())
    result = service.ensure_stock_subscriptions(codes)
    failed = result.get("failed") or {}
    return {
        "requested": len(codes),
        "newlySubscribed": len(result.get("newly_subscribed") or []),
        "alreadySubscribed": len(result.get("already_subscribed") or []),
        "failed": len(failed),
        "failedSample": dict(list(failed.items())[:5]),
        "mainActive": result.get("main_active_count"),
        "active": result.get("active_count"),
    }


def status() -> dict:
    return dict(_status)


def _loop() -> None:
    backoff = 0
    while True:
        wait = POLL_SECONDS
        try:
            if in_subscribe_window():
                summary = subscribe_once()
                summary["at"] = datetime.now(TW_TZ).isoformat(timespec="seconds")
                _status.clear()
                _status.update(summary)
                if summary.get("newlySubscribed") or summary.get("failed"):
                    logger.info("族群股票逐筆訂閱：%s", summary)
                if summary.get("failed") and not summary.get("newlySubscribed"):
                    # 這一輪一檔都沒訂到（多半是共享連線登入失敗）：2、4、8…分鐘後再試，最多 15 分鐘
                    backoff = min(MAX_BACKOFF_SECONDS, max(120, backoff * 2))
                    wait = backoff
                else:
                    backoff = 0
            else:
                backoff = 0
        except Exception:  # noqa: BLE001
            logger.exception("族群股票逐筆訂閱失敗")
        time.sleep(wait)


def start_stock_universe_subscriber() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_STOCK_UNIVERSE_SUBSCRIBE_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            logger.info("族群股票逐筆訂閱已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-stock-universe-subscriber", daemon=True).start()
        _started = True
        logger.info("族群股票逐筆訂閱已啟動，間隔=%ss", POLL_SECONDS)
        return True
