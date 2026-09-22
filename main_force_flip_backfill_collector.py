"""主力累計翻多空訊號的收盤後回補排程。

13:40 後重播今天（每分鐘價量用 Shioaji kbars、主力張數用已落盤的主力副圖），補回
偵測器不在線時漏掉的訊號。當天歷史額度已經用完時（2026-09-22 就是：偵測器 13:13 才
上線、額度又早被個股回補燒光）不算完成，隔天開盤前（08:55 前）額度恢復時再把前一個
交易日補回來。完成狀態存 SQLite，重新部署不會重跑。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection
from main_force_flip_signals import flip_signal_backfill_status, start_flip_signal_backfill

logger = logging.getLogger("hanstock.main_force_flip_backfill_collector")
TW_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = max(60, int(os.getenv("HANSTOCK_MAIN_FORCE_FLIP_BACKFILL_SECONDS", "600")))
SETTLE_HOUR, SETTLE_MINUTE = 13, 40
MORNING_CUTOFF_HOUR, MORNING_CUTOFF_MINUTE = 8, 55
_started = False
_lock = threading.Lock()


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS main_force_flip_backfill_state (
            trade_date TEXT PRIMARY KEY,
            done INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            updated_at TEXT NOT NULL
        )"""
    )


def backfill_done(trade_date: str) -> bool:
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute(
            "SELECT done FROM main_force_flip_backfill_state WHERE trade_date = ?", (trade_date,)
        ).fetchone()
    return bool(row and row["done"])


def _mark(trade_date: str, done: bool, result: dict[str, Any]) -> None:
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.execute(
            """INSERT INTO main_force_flip_backfill_state (trade_date, done, result_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(trade_date) DO UPDATE SET
                   done = excluded.done, result_json = excluded.result_json, updated_at = excluded.updated_at""",
            (trade_date, 1 if done else 0, json.dumps(result, ensure_ascii=False), updated_at),
        )


def previous_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def _run_and_wait(trade_date: str) -> dict[str, Any]:
    started = start_flip_signal_backfill(trade_date)
    if not started.get("started"):
        return {"tradeDate": trade_date, "error": "backfill_already_running"}
    while flip_signal_backfill_status().get("running"):
        time.sleep(1.0)
    return flip_signal_backfill_status().get("result") or {"tradeDate": trade_date, "error": "no_result"}


def pending_trade_dates(now: datetime) -> list[str]:
    """這一輪該補的日期：收盤後補今天；開盤前補前一個交易日（額度用完那天沒補成的）。"""
    targets: list[str] = []
    today = now.date()
    if today.weekday() < 5 and (now.hour, now.minute) >= (SETTLE_HOUR, SETTLE_MINUTE):
        targets.append(today.isoformat())
    if (now.hour, now.minute) < (MORNING_CUTOFF_HOUR, MORNING_CUTOFF_MINUTE):
        targets.append(previous_weekday(today).isoformat())
    return [trade_date for trade_date in targets if not backfill_done(trade_date)]


def collect_once(*, now: datetime | None = None, run_backfill: Callable[[str], dict[str, Any]] | None = None) -> dict:
    now = now or datetime.now(TW_TZ)
    targets = pending_trade_dates(now)
    if not targets:
        return {"skipped": "nothing_to_do"}
    trade_date = targets[0]
    result = (run_backfill or _run_and_wait)(trade_date)
    done = not result.get("error") and not result.get("quotaBlocked")
    _mark(trade_date, done, result)
    return {"tradeDate": trade_date, "done": done, "result": result}


def _loop() -> None:
    while True:
        try:
            result = collect_once()
            if "skipped" not in result:
                logger.info("主力累計翻多空回補排程: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("主力累計翻多空回補排程失敗")
        time.sleep(POLL_SECONDS)


def start_main_force_flip_backfill_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_MAIN_FORCE_FLIP_BACKFILL_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return False
        threading.Thread(target=_loop, name="hanstock-main-force-flip-backfill", daemon=True).start()
        _started = True
        return True
