"""88（開低站上VWAP）／87（開高跌破VWAP）的收盤後回補排程。

13:40 後用今天完整的 1 分 K（Shioaji kbars，跟主力翻多空回補共用快取）重播今天，以重播結果為準；
歷史額度用完沒補成的，隔天開盤前（08:55 前）再補前一個交易日。完成狀態連同規則版本存 SQLite，
重新部署不會重跑；GAP_VWAP_RULES_VERSION 一改，已完成的日期會再重播一次。
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
from gap_vwap_signals import GAP_VWAP_RULES_VERSION, gap_vwap_backfill_status, start_gap_vwap_backfill

logger = logging.getLogger("hanstock.gap_vwap_backfill_collector")
TW_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = max(60, int(os.getenv("HANSTOCK_GAP_VWAP_BACKFILL_SECONDS", "600")))
SETTLE_HOUR, SETTLE_MINUTE = 13, 40
MORNING_CUTOFF_HOUR, MORNING_CUTOFF_MINUTE = 8, 55
_started = False
_lock = threading.Lock()


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS gap_vwap_backfill_state (
            trade_date TEXT PRIMARY KEY,
            done INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            updated_at TEXT NOT NULL
        )"""
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(gap_vwap_backfill_state)").fetchall()}
    if "rules_version" not in columns:
        connection.execute(
            "ALTER TABLE gap_vwap_backfill_state ADD COLUMN rules_version INTEGER NOT NULL DEFAULT 1"
        )


def backfill_done(trade_date: str) -> bool:
    """這一天已經用「目前這一版規則」重播完成；舊版規則做的完成紀錄不算。"""
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute(
            "SELECT done, rules_version FROM gap_vwap_backfill_state WHERE trade_date = ?", (trade_date,)
        ).fetchone()
    return bool(row and row["done"] and int(row["rules_version"] or 0) == GAP_VWAP_RULES_VERSION)


def _mark(trade_date: str, done: bool, result: dict[str, Any]) -> None:
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.execute(
            """INSERT INTO gap_vwap_backfill_state (trade_date, done, result_json, updated_at, rules_version)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(trade_date) DO UPDATE SET
                   done = excluded.done, result_json = excluded.result_json, updated_at = excluded.updated_at,
                   rules_version = excluded.rules_version""",
            (trade_date, 1 if done else 0, json.dumps(result, ensure_ascii=False), updated_at, GAP_VWAP_RULES_VERSION),
        )


def previous_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def _run_and_wait(trade_date: str) -> dict[str, Any]:
    started = start_gap_vwap_backfill(trade_date)
    if not started.get("started"):
        return {"tradeDate": trade_date, "error": "backfill_already_running"}
    while gap_vwap_backfill_status().get("running"):
        time.sleep(1.0)
    return gap_vwap_backfill_status().get("result") or {"tradeDate": trade_date, "error": "no_result"}


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
                logger.info("88/87 開低開高VWAP回補排程: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("88/87 開低開高VWAP回補排程失敗")
        time.sleep(POLL_SECONDS)


def start_gap_vwap_backfill_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_GAP_VWAP_BACKFILL_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return False
        threading.Thread(target=_loop, name="hanstock-gap-vwap-backfill", daemon=True).start()
        _started = True
        return True
