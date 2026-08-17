"""巡檢最近開啟過的個股 K 線，遇到缺漏或斷線後自動回補。"""

from __future__ import annotations

import logging
import os
import threading
import time

from stock_bar_bootstrap import repair_recent_stock_bars_once

logger = logging.getLogger("hanstock.stock_bar_repair_collector")
POLL_SECONDS = max(10, int(os.getenv("HANSTOCK_STOCK_BAR_REPAIR_SECONDS", "15")))
_started = False
_lock = threading.Lock()


def collect_once(*, service=None) -> dict:
    return repair_recent_stock_bars_once(service=service)


def _loop() -> None:
    while True:
        try:
            result = collect_once()
            if result["checkedCount"] or result["failedCount"]:
                logger.info("個股 K 線自動巡檢: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("個股 K 線自動巡檢失敗")
        time.sleep(POLL_SECONDS)


def start_stock_bar_repair_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        disabled = os.getenv("HANSTOCK_STOCK_BAR_REPAIR_ENABLED", "true").strip().lower()
        if disabled in {"0", "false", "no", "off"}:
            return False
        threading.Thread(
            target=_loop,
            name="hanstock-stock-bar-repair-collector",
            daemon=True,
        ).start()
        _started = True
        logger.info("個股 K 線自動巡檢已啟動，間隔=%ss", POLL_SECONDS)
        return True
