"""日K背景收集器：資料來源是官方 TWSE/TPEx 盤後資料（official_daily_bars.py），
跟 Shioaji 完全無關，不佔用即時報價訂閱額度。第一次啟動（bars_1d 是空的）會回補
約一年份；之後每次只補最近幾天，避免每輪都重新下載一整年。"""

from __future__ import annotations

import logging
import os
import threading
import time

from daily_bars_store import daily_bars_storage_status, prune_old_daily_bars
from official_daily_bars import download_official_daily_bars

logger = logging.getLogger("hanstock.daily_bars_collector")
POLL_SECONDS = max(1800, int(os.getenv("HANSTOCK_DAILY_BARS_COLLECTOR_SECONDS", str(6 * 60 * 60))))
BACKFILL_DAYS = max(30, int(os.getenv("HANSTOCK_DAILY_BARS_BACKFILL_DAYS", "370")))
CATCHUP_DAYS = max(1, int(os.getenv("HANSTOCK_DAILY_BARS_CATCHUP_DAYS", "5")))
KEEP_DAYS = max(30, int(os.getenv("HANSTOCK_DAILY_BARS_KEEP_DAYS", "365")))
_started = False
_lock = threading.Lock()


def collect_once() -> dict:
    is_first_run = daily_bars_storage_status()["barCount"] == 0
    days = BACKFILL_DAYS if is_first_run else CATCHUP_DAYS
    result = download_official_daily_bars(days=days, run_triangle_scan=False)
    result["mode"] = "backfill" if is_first_run else "catchup"
    result["pruned"] = prune_old_daily_bars(KEEP_DAYS)
    return result


def _loop() -> None:
    while True:
        try:
            result = collect_once()
            logger.info("日K已更新: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("日K背景收集失敗")
        time.sleep(POLL_SECONDS)


def start_daily_bars_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_DAILY_BARS_COLLECTOR_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            logger.info("日K背景收集器已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-daily-bars-collector", daemon=True).start()
        _started = True
        logger.info("日K背景收集器已啟動，間隔=%ss，保留=%s個交易日", POLL_SECONDS, KEEP_DAYS)
        return True
