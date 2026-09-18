"""盤後定價交易背景收集器：14:30撮合公布後抓一次就好，不用一直重抓。"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from after_hours_fixed_price import fetch_after_hours_day, load_after_hours_day, save_after_hours_day

logger = logging.getLogger("hanstock.after_hours_fixed_price_collector")
TW_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = max(60, int(os.getenv("HANSTOCK_AFTER_HOURS_COLLECTOR_SECONDS", "300")))
_started = False
_lock = threading.Lock()


def collect_once() -> dict:
    now = datetime.now(TW_TZ)
    trade_date_str = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5:
        return {"skipped": "weekend", "tradeDate": trade_date_str}
    if (now.hour, now.minute) < (14, 35):
        return {"skipped": "before_after_hours_session", "tradeDate": trade_date_str}
    if load_after_hours_day(trade_date_str, limit=1):
        return {"skipped": "already_collected", "tradeDate": trade_date_str}
    entries = fetch_after_hours_day(now.date())
    saved = save_after_hours_day(trade_date_str, entries)
    return {"tradeDate": trade_date_str, "fetched": len(entries), "saved": saved}


def _loop() -> None:
    while True:
        try:
            result = collect_once()
            logger.info("盤後定價交易已檢查: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("盤後定價交易收集失敗")
        time.sleep(POLL_SECONDS)


def start_after_hours_fixed_price_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_AFTER_HOURS_COLLECTOR_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            logger.info("盤後定價交易收集器已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-after-hours-fixed-price", daemon=True).start()
        _started = True
        logger.info("盤後定價交易收集器已啟動，間隔=%ss", POLL_SECONDS)
        return True
