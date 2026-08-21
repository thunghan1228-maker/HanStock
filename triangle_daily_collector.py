"""交易日盤後自動更新官方日 K，完成後重跑日線三角收斂。"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger("hanstock.triangle_daily_collector")
TW_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = max(60, int(os.getenv("HANSTOCK_TRIANGLE_DAILY_SECONDS", "900")))
READY_HOUR = max(13, min(23, int(os.getenv("HANSTOCK_TRIANGLE_DAILY_HOUR", "14"))))
READY_MINUTE = max(0, min(59, int(os.getenv("HANSTOCK_TRIANGLE_DAILY_MINUTE", "30"))))

_started = False
_start_lock = threading.Lock()
_collect_lock = threading.Lock()
_status: dict[str, Any] = {
    "status": "not_started",
    "targetDate": None,
    "lastAttemptAt": None,
    "lastSuccessAt": None,
    "insertedBars": 0,
    "matchedCount": 0,
    "twseRowCount": 0,
    "tpexRowCount": 0,
    "error": None,
}


def triangle_daily_collector_status() -> dict[str, Any]:
    with _collect_lock:
        return dict(_status)


def collect_once(
    *,
    now: datetime | None = None,
    twse_loader: Callable[..., list[dict[str, Any]]] | None = None,
    tpex_loader: Callable[..., list[dict[str, Any]]] | None = None,
    save_day: Callable[[list[dict[str, Any]]], int] | None = None,
    scanner: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current = now.astimezone(TW_TZ) if now else datetime.now(TW_TZ)
    target = current.date()
    attempt_at = current.isoformat(timespec="seconds")

    with _collect_lock:
        if current.weekday() >= 5:
            _status.update(status="market_closed", targetDate=target.isoformat(), lastAttemptAt=attempt_at)
            return dict(_status)
        if current.timetz().replace(tzinfo=None) < datetime_time(READY_HOUR, READY_MINUTE):
            _status.update(status="waiting_after_close", targetDate=target.isoformat(), lastAttemptAt=attempt_at)
            return dict(_status)
        if _status.get("status") == "completed" and _status.get("targetDate") == target.isoformat():
            return dict(_status)
        _status.update(
            status="running",
            targetDate=target.isoformat(),
            lastAttemptAt=attempt_at,
            error=None,
        )

    from official_daily_bars import _save_day, fetch_tpex_day, fetch_twse_day
    from triangle_screener import scan_all_triangles

    twse_fetch = twse_loader or fetch_twse_day
    tpex_fetch = tpex_loader or fetch_tpex_day
    persist = save_day or _save_day
    run_scan = scanner or scan_all_triangles

    try:
        twse_rows = twse_fetch(target)
        if not twse_rows:
            with _collect_lock:
                _status.update(
                    status="waiting_official_data",
                    twseRowCount=0,
                    tpexRowCount=0,
                    error="證交所盤後資料尚未公布或回傳空白",
                )
                return dict(_status)

        try:
            tpex_rows = tpex_fetch(target)
        except Exception as exc:  # noqa: BLE001
            logger.warning("櫃買盤後資料暫時取得失敗，保留重試狀態: %s", exc)
            with _collect_lock:
                _status.update(
                    status="waiting_official_data",
                    twseRowCount=len(twse_rows),
                    tpexRowCount=0,
                    error=f"櫃買盤後資料取得失敗：{exc}",
                )
                return dict(_status)

        if not tpex_rows:
            with _collect_lock:
                _status.update(
                    status="waiting_official_data",
                    twseRowCount=len(twse_rows),
                    tpexRowCount=0,
                    error="櫃買盤後資料尚未公布或回傳空白",
                )
                return dict(_status)

        rows = [*twse_rows, *tpex_rows]
        inserted = persist(rows)
        scan = run_scan()
        summary = scan.get("summary") or {}
        success_at = datetime.now(TW_TZ).isoformat(timespec="seconds")
        with _collect_lock:
            _status.update(
                status="completed",
                targetDate=target.isoformat(),
                lastSuccessAt=success_at,
                insertedBars=int(inserted),
                matchedCount=int(summary.get("matched_count") or 0),
                twseRowCount=len(twse_rows),
                tpexRowCount=len(tpex_rows),
                error=None,
            )
            return dict(_status)
    except Exception as exc:  # noqa: BLE001
        logger.exception("盤後三角收斂自動更新失敗")
        with _collect_lock:
            _status.update(status="error", error=str(exc))
            return dict(_status)


def _loop() -> None:
    while True:
        collect_once()
        time.sleep(POLL_SECONDS)


def start_triangle_daily_collector() -> bool:
    global _started
    with _start_lock:
        if _started:
            return False
        disabled = os.getenv("HANSTOCK_TRIANGLE_DAILY_ENABLED", "true").strip().lower()
        if disabled in {"0", "false", "no", "off"}:
            return False
        threading.Thread(
            target=_loop,
            name="hanstock-triangle-daily-collector",
            daemon=True,
        ).start()
        _started = True
        logger.info(
            "盤後三角收斂自動更新已啟動，%02d:%02d 後每 %ss 重試",
            READY_HOUR,
            READY_MINUTE,
            POLL_SECONDS,
        )
        return True
