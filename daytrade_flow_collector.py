"""每天 00:05 後自動補存上一個有效交易日的全市場隔日沖推估。"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from daytrade_flow import latest_completed_trade_date, start_full_market_scan
from daytrade_flow_store import has_completed_daytrade_scan, load_daytrade_scan_status
from otc_index import TW_TZ

logger = logging.getLogger("hanstock.daytrade_flow_collector")

POLL_SECONDS = max(60, int(os.getenv("HANSTOCK_DAYTRADE_COLLECTOR_SECONDS", "300")))
_started = False
_lock = threading.Lock()


def collect_once(now: datetime | None = None) -> bool:
    current = now.astimezone(TW_TZ) if now is not None else datetime.now(TW_TZ)
    # 00:00~00:04 讓前一日資料來源完成結算；其後任何時間都可補漏。
    if current.hour == 0 and current.minute < 5:
        return False
    trade_date = latest_completed_trade_date(current)
    if has_completed_daytrade_scan(trade_date):
        return False
    status = load_daytrade_scan_status(trade_date)
    if status.get("status") == "running":
        return False

    from quote_service import get_quote_service

    service = get_quote_service()
    if not bool(getattr(getattr(service, "state", None), "logged_in", False)):
        return False
    started = start_full_market_scan(service, trade_date)
    if started:
        logger.info("每日隔日沖全市場備份已啟動: trade_date=%s", trade_date)
    return started


def _loop() -> None:
    while True:
        try:
            collect_once()
        except Exception:  # noqa: BLE001
            # 失敗不刪舊資料；下一輪或服務重啟後會自動續跑。
            logger.exception("每日隔日沖備份排程例外，稍後重試")
        time.sleep(POLL_SECONDS)


def start_daytrade_flow_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        disabled = os.getenv("HANSTOCK_DAYTRADE_COLLECTOR_ENABLED", "true").strip().lower()
        if disabled in {"0", "false", "no", "off"}:
            logger.info("每日隔日沖備份排程已停用")
            return False
        thread = threading.Thread(
            target=_loop,
            name="hanstock-daytrade-flow-collector",
            daemon=True,
        )
        thread.start()
        _started = True
        logger.info("每日隔日沖備份排程已啟動：00:05 後補存最近交易日，間隔=%ss", POLL_SECONDS)
        return True
