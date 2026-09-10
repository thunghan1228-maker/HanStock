"""盤中訊號收集器（停用版）。Rule1/盤中訊號已移除，保留 API 名稱避免舊程式匯入失敗。"""

from __future__ import annotations

import logging

logger = logging.getLogger("hanstock.intraday_signal_collector")


def collect_once() -> bool:
    return False


def start_intraday_signal_collector() -> bool:
    logger.info("Rule1／盤中訊號收集器已永久停用")
    return False
