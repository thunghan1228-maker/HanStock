"""盤中早盤隔日沖賣壓訊號背景收集器。"""

from __future__ import annotations

import logging
import os
import threading
import time

from daytrade_early_sell import collect_early_sell_signals


logger = logging.getLogger("hanstock.daytrade_early_sell_collector")
POLL_SECONDS = max(5, int(os.getenv("HANSTOCK_EARLY_SELL_COLLECTOR_SECONDS", "10")))
_started = False
_lock = threading.Lock()


def collect_once() -> dict:
    from market_data_hub import get_market_data_hub
    from quote_service import get_quote_service

    service = get_quote_service()
    if not bool(getattr(getattr(service, "state", None), "logged_in", False)):
        return {"prepared": False, "inserted": [], "reason": "shioaji_not_ready"}
    return collect_early_sell_signals(service, get_market_data_hub())


def _loop() -> None:
    while True:
        try:
            result = collect_once()
            if result.get("inserted"):
                logger.info("早盤大單賣壓 50%% 訊號新增 %s 筆", len(result["inserted"]))
        except Exception:  # noqa: BLE001
            logger.exception("早盤大單賣壓訊號收集器例外")
        time.sleep(POLL_SECONDS)


def start_daytrade_early_sell_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        disabled = os.getenv("HANSTOCK_EARLY_SELL_COLLECTOR_ENABLED", "true").strip().lower()
        if disabled in {"0", "false", "no", "off"}:
            logger.info("早盤大單賣壓訊號收集器已停用")
            return False
        threading.Thread(
            target=_loop,
            name="hanstock-daytrade-early-sell-collector",
            daemon=True,
        ).start()
        _started = True
        logger.info("早盤大單賣壓訊號收集器已啟動，間隔=%ss", POLL_SECONDS)
        return True
