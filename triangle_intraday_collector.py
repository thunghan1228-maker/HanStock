"""盤中三角收斂收集器（停用版）。保留匯入介面以相容舊程式。"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("hanstock.triangle_intraday_collector")


def collect_once(now: datetime | None = None) -> dict | None:
    return None


def start_triangle_intraday_collector() -> bool:
    logger.info("盤中三角收斂收集器已永久停用")
    return False
