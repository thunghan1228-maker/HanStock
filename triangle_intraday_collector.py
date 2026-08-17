"""盤中日線三角收斂五分鐘背景掃描器。"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from triangle_intraday import record_intraday_triangle_error, scan_intraday_triangles

logger = logging.getLogger("hanstock.triangle_intraday_collector")
TAIPEI = ZoneInfo("Asia/Taipei")
POLL_SECONDS = max(10, int(os.getenv("HANSTOCK_TRIANGLE_INTRADAY_COLLECTOR_SECONDS", "20")))
_started = False
_lock = threading.Lock()
_last_success_bucket: int | None = None


def _scan_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return 9 * 60 <= minute <= 13 * 60 + 30


def collect_once(now: datetime | None = None) -> dict | None:
    global _last_success_bucket
    current = now.astimezone(TAIPEI) if now is not None else datetime.now(TAIPEI)
    if not _scan_window(current):
        return None
    # 每個五分鐘區間第一個 10 秒讓行情欄位先完成刷新。
    if current.minute % 5 == 0 and current.second < 10:
        return None
    bucket = int(current.timestamp() // 300)
    if _last_success_bucket == bucket:
        return None
    try:
        result = scan_intraday_triangles(current)
    except Exception as exc:  # noqa: BLE001
        record_intraday_triangle_error(exc, current)
        raise
    _last_success_bucket = bucket
    logger.info(
        "盤中三角收斂更新完成: date=%s candidates=%s quotes=%s matched=%s signals=%s",
        result.get("trade_date"),
        result.get("summary", {}).get("candidate_count"),
        result.get("summary", {}).get("quote_count"),
        result.get("summary", {}).get("matched_count"),
        result.get("summary", {}).get("inserted_signal_count"),
    )
    return result


def _loop() -> None:
    while True:
        try:
            collect_once()
        except Exception:  # noqa: BLE001
            logger.exception("盤中三角收斂背景掃描失敗，保留上一版並於本區間重試")
        time.sleep(POLL_SECONDS)


def start_triangle_intraday_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_TRIANGLE_INTRADAY_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            logger.info("盤中三角收斂掃描器已停用")
            return False
        thread = threading.Thread(target=_loop, name="hanstock-triangle-intraday", daemon=True)
        thread.start()
        _started = True
        logger.info("盤中三角收斂掃描器已啟動，每五分鐘自動更新")
        return True
