"""巡檢最近開啟過的個股 K 線，遇到缺漏或斷線後自動回補。"""

from __future__ import annotations

import logging
import os
import threading
import time

from stock_bar_bootstrap import repair_recent_stock_bars_once
from main_force_backfill_jobs import process_main_force_backfill_job

logger = logging.getLogger("hanstock.stock_bar_repair_collector")
POLL_SECONDS = max(10, int(os.getenv("HANSTOCK_STOCK_BAR_REPAIR_SECONDS", "15")))
# 全族群(664檔股票)一次排進回補佇列後，一輪只處理1個工作要跑好幾個小時
# 才能全部補完；每輪多處理幾個，配合既有的history_quota共用額度保護，
# 不會因為調高這個數字就超用Shioaji歷史查詢額度。
BACKFILL_JOBS_PER_CYCLE = max(1, int(os.getenv("HANSTOCK_MAIN_FORCE_BACKFILL_JOBS_PER_CYCLE", "5")))
_started = False
_lock = threading.Lock()


def collect_once(*, service=None) -> dict:
    jobs = []
    for _ in range(BACKFILL_JOBS_PER_CYCLE):
        job = process_main_force_backfill_job(service=service)
        if job is None:
            break
        jobs.append(job)
    if jobs:
        logger.info("指定交易日主力回補: %s", jobs)
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
