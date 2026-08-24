"""前後 20 族群即時大單候選訂閱維護器。"""

from __future__ import annotations

import logging
import os
import threading
import time

from intraday_large_order import get_intraday_large_order_monitor, refresh_intraday_large_order_candidates

logger = logging.getLogger("hanstock.intraday_large_order_collector")
POLL_SECONDS = max(5, int(os.getenv("HANSTOCK_INSTANT_LARGE_REFRESH_SECONDS", "15")))
_started = False
_lock = threading.Lock()


def collect_once() -> dict:
    from quote_service import get_quote_service
    service = get_quote_service()
    if not bool(getattr(getattr(service, "state", None), "logged_in", False)):
        return {"prepared": False, "reason": "shioaji_not_ready"}
    return refresh_intraday_large_order_candidates(service)


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
