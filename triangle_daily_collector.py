"""盤後型態選股收集器（停用版）。三角收斂與 VCP 已移除。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("hanstock.triangle_daily_collector")

_status: dict[str, Any] = {
    "status": "disabled",
    "targetDate": None,
    "lastAttemptAt": None,
    "lastSuccessAt": None,
    "insertedBars": 0,
    "matchedCount": 0,
    "vcpMatchedCount": 0,
    "twseRowCount": 0,
    "tpexRowCount": 0,
    "error": "三角收斂與 VCP 已停用",
}


def triangle_daily_collector_status() -> dict[str, Any]:
    return dict(_status)


def collect_once(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return dict(_status)


def start_triangle_daily_collector() -> bool:
    logger.info("三角收斂／VCP 盤後收集器已永久停用")
    return False
