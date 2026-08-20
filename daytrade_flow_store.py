"""疑似隔日沖全市場掃描結果的 Railway SQLite 永久保存。"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Iterable

from database import get_connection
from otc_index import TW_TZ


_initialize_lock = threading.Lock()


def _now() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def _initialize() -> None:
    with _initialize_lock:
        with get_connection() as connection:
            connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS daytrade_flow_daily_v2 (
                ticker TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                category TEXT NOT NULL,
                open_price REAL NOT NULL DEFAULT 0,
                high_price REAL NOT NULL DEFAULT 0,
                close_price REAL NOT NULL DEFAULT 0,
                reference_price REAL NOT NULL DEFAULT 0,
                limit_up_price REAL NOT NULL DEFAULT 0,
                day_change_pct REAL NOT NULL DEFAULT 0,
                large_buy_amount REAL NOT NULL DEFAULT 0,
                large_sell_amount REAL NOT NULL DEFAULT 0,
                total_turnover_amount REAL NOT NULL DEFAULT 0,
                late_large_buy_amount REAL NOT NULL DEFAULT 0,
                price_impact_pct REAL NOT NULL DEFAULT 0,
                previous_large_buy_amount REAL NOT NULL DEFAULT 0,
                next_day_large_sell_amount REAL NOT NULL DEFAULT 0,
                suspicion_score REAL NOT NULL DEFAULT 0,
                main_force_data_status TEXT NOT NULL DEFAULT 'historical_ticks',
                main_force_data_available INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (ticker, trade_date)
            );
            CREATE INDEX IF NOT EXISTS idx_daytrade_flow_v2_date_category
                ON daytrade_flow_daily_v2 (trade_date DESC, category, suspicion_score DESC);

            CREATE TABLE IF NOT EXISTS daytrade_flow_scan_jobs (
                trade_date TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                requested_count INTEGER NOT NULL DEFAULT 0,
                processed_count INTEGER NOT NULL DEFAULT 0,
                match_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                data_missing_count INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT NOT NULL DEFAULT '[]',
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );
                """
            )
            row_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(daytrade_flow_daily_v2)").fetchall()
            }
            if "main_force_data_status" not in row_columns:
                connection.execute(
                    "ALTER TABLE daytrade_flow_daily_v2 ADD COLUMN main_force_data_status TEXT NOT NULL DEFAULT 'historical_ticks'"
                )
            if "main_force_data_available" not in row_columns:
                connection.execute(
                    "ALTER TABLE daytrade_flow_daily_v2 ADD COLUMN main_force_data_available INTEGER NOT NULL DEFAULT 1"
                )
            job_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(daytrade_flow_scan_jobs)").fetchall()
            }
            if "data_missing_count" not in job_columns:
                connection.execute(
                    "ALTER TABLE daytrade_flow_scan_jobs ADD COLUMN data_missing_count INTEGER NOT NULL DEFAULT 0"
                )


def begin_daytrade_scan(trade_date: str, requested_count: int) -> None:
    _initialize()
    now = _now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO daytrade_flow_scan_jobs (
                trade_date, status, requested_count, processed_count,
                match_count, error_count, errors_json, started_at,
                completed_at, updated_at
            ) VALUES (?, 'running', ?, 0, 0, 0, '[]', ?, NULL, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                status = 'running',
                requested_count = excluded.requested_count,
                processed_count = 0,
                match_count = 0,
                error_count = 0,
                data_missing_count = 0,
                errors_json = '[]',
                started_at = excluded.started_at,
                completed_at = NULL,
                updated_at = excluded.updated_at
            """,
            (trade_date, max(0, int(requested_count)), now, now),
        )


def save_daytrade_rows(rows: Iterable[dict[str, Any]]) -> int:
    _initialize()
    normalized = []
    now = _now()
    for row in rows:
        normalized.append(
            (
                str(row.get("ticker") or "").strip().upper(),
                str(row.get("trade_date") or "").strip(),
                str(row.get("name") or row.get("ticker") or "").strip(),
                str(row.get("market") or "上市櫃").strip(),
                str(row.get("category") or "強勢大單").strip(),
                float(row.get("open_price") or 0),
                float(row.get("high_price") or 0),
                float(row.get("close_price") or 0),
                float(row.get("reference_price") or 0),
                float(row.get("limit_up_price") or 0),
                float(row.get("day_change_pct") or 0),
                float(row.get("large_buy_amount") or 0),
                float(row.get("large_sell_amount") or 0),
                float(row.get("total_turnover_amount") or 0),
                float(row.get("late_large_buy_amount") or 0),
                float(row.get("price_impact_pct") or 0),
                float(row.get("previous_large_buy_amount") or 0),
                float(row.get("next_day_large_sell_amount") or 0),
                float(row.get("suspicion_score") or 0),
                str(row.get("main_force_data_status") or "historical_ticks"),
                1 if bool(row.get("main_force_data_available", True)) else 0,
                now,
            )
        )
    normalized = [row for row in normalized if row[0] and row[1]]
    if not normalized:
        return 0
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO daytrade_flow_daily_v2 (
                ticker, trade_date, name, market, category,
                open_price, high_price, close_price, reference_price,
                limit_up_price, day_change_pct, large_buy_amount,
                large_sell_amount, total_turnover_amount,
                late_large_buy_amount, price_impact_pct,
                previous_large_buy_amount, next_day_large_sell_amount,
                suspicion_score, main_force_data_status,
                main_force_data_available, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, trade_date) DO UPDATE SET
                name = excluded.name,
                market = excluded.market,
                category = excluded.category,
                open_price = excluded.open_price,
                high_price = excluded.high_price,
                close_price = excluded.close_price,
                reference_price = excluded.reference_price,
                limit_up_price = excluded.limit_up_price,
                day_change_pct = excluded.day_change_pct,
                large_buy_amount = excluded.large_buy_amount,
                large_sell_amount = excluded.large_sell_amount,
                total_turnover_amount = excluded.total_turnover_amount,
                late_large_buy_amount = excluded.late_large_buy_amount,
                price_impact_pct = excluded.price_impact_pct,
                previous_large_buy_amount = excluded.previous_large_buy_amount,
                next_day_large_sell_amount = excluded.next_day_large_sell_amount,
                suspicion_score = excluded.suspicion_score,
                main_force_data_status = excluded.main_force_data_status,
                main_force_data_available = excluded.main_force_data_available,
                updated_at = excluded.updated_at
            """,
            normalized,
        )
    return len(normalized)


def update_daytrade_scan_progress(
    trade_date: str,
    *,
    processed_count: int,
    match_count: int,
    errors: Iterable[str] = (),
) -> None:
    _initialize()
    error_list = list(errors)[-20:]
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE daytrade_flow_scan_jobs
            SET processed_count = ?, match_count = ?, error_count = ?,
                errors_json = ?, updated_at = ?
            WHERE trade_date = ?
            """,
            (
                max(0, int(processed_count)),
                max(0, int(match_count)),
                len(error_list),
                json.dumps(error_list, ensure_ascii=False),
                _now(),
                trade_date,
            ),
        )


def finish_daytrade_scan(
    trade_date: str,
    rows: list[dict[str, Any]],
    *,
    processed_count: int,
    errors: Iterable[str] = (),
    incomplete_count: int = 0,
) -> None:
    """完成時原子替換該交易日名單；空的市場資料絕不覆蓋舊備份。"""
    _initialize()
    if processed_count <= 0:
        fail_daytrade_scan(trade_date, "市場資料為空，保留既有備份")
        return
    save_daytrade_rows(rows)
    tickers = [str(row.get("ticker") or "").strip().upper() for row in rows]
    now = _now()
    error_list = list(errors)[-20:]
    with get_connection() as connection:
        if tickers:
            placeholders = ",".join("?" for _ in tickers)
            connection.execute(
                f"DELETE FROM daytrade_flow_daily_v2 WHERE trade_date = ? AND ticker NOT IN ({placeholders})",
                (trade_date, *tickers),
            )
        else:
            # 有完整市場但零筆符合時，仍保留舊資料，避免錯誤條件清空歷史。
            error_list.append("零筆符合，為安全起見保留原有備份")
        connection.execute(
            """
            UPDATE daytrade_flow_scan_jobs
            SET status = ?, processed_count = ?, match_count = ?,
                error_count = ?, data_missing_count = ?, errors_json = ?, completed_at = ?, updated_at = ?
            WHERE trade_date = ?
            """,
            (
                "partial" if incomplete_count > 0 else "completed",
                int(processed_count),
                len(rows),
                len(error_list),
                max(0, int(incomplete_count)),
                json.dumps(error_list, ensure_ascii=False),
                now,
                now,
                trade_date,
            ),
        )


def fail_daytrade_scan(trade_date: str, message: str) -> None:
    _initialize()
    now = _now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO daytrade_flow_scan_jobs (
                trade_date, status, errors_json, updated_at, completed_at
            ) VALUES (?, 'failed', ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                status = 'failed', errors_json = excluded.errors_json,
                completed_at = excluded.completed_at, updated_at = excluded.updated_at
            """,
            (trade_date, json.dumps([message], ensure_ascii=False), now, now),
        )


def load_daytrade_rows(trade_date: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    _initialize()
    with get_connection() as connection:
        if not trade_date:
            found = connection.execute(
                "SELECT MAX(trade_date) AS trade_date FROM daytrade_flow_daily_v2"
            ).fetchone()
            trade_date = str(found["trade_date"] or "") if found else ""
        if not trade_date:
            return []
        rows = connection.execute(
            """
            SELECT * FROM daytrade_flow_daily_v2
            WHERE trade_date = ?
            ORDER BY
                CASE category
                    WHEN '漲停鎖定' THEN 1
                    WHEN '曾達漲停' THEN 2
                    ELSE 3
                END,
                suspicion_score DESC,
                large_buy_amount DESC
            LIMIT ?
            """,
            (trade_date, max(1, min(int(limit), 2000))),
        ).fetchall()
    return [dict(row) for row in rows]


def load_daytrade_scan_status(trade_date: str | None = None) -> dict[str, Any]:
    _initialize()
    with get_connection() as connection:
        if trade_date:
            row = connection.execute(
                "SELECT * FROM daytrade_flow_scan_jobs WHERE trade_date = ?",
                (trade_date,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM daytrade_flow_scan_jobs ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
    if not row:
        return {"status": "not_started", "trade_date": trade_date}
    data = dict(row)
    try:
        data["errors"] = json.loads(str(data.pop("errors_json") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        data["errors"] = []
    return data


def has_completed_daytrade_scan(trade_date: str) -> bool:
    return load_daytrade_scan_status(trade_date).get("status") == "completed"
