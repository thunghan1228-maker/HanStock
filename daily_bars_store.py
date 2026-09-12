"""日K（bars_1d）查詢與保留政策；資料表本身由 database.py 建立，
寫入由 official_daily_bars.py 負責（官方 TWSE/TPEx 盤後資料，跟 Shioaji 無關）。"""

from __future__ import annotations

from typing import Any

from database import get_connection, initialize_database


def load_daily_bars(stock_code: str, limit: int = 260) -> list[dict[str, Any]]:
    """讀取個股日K，由舊到新排序。"""
    initialize_database()
    code = str(stock_code).strip().upper()
    limit = max(1, min(int(limit), 2000))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT bar_time, open, high, low, close, volume
            FROM bars_1d
            WHERE stock_code = ?
            ORDER BY bar_time DESC
            LIMIT ?
            """,
            (code, limit),
        ).fetchall()
    rows = list(reversed(rows))
    return [{
        "ts": row["bar_time"],
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": int(row["volume"]),
    } for row in rows]


def prune_old_daily_bars(keep_days: int = 365) -> int:
    """只保留最近 keep_days 個「有資料的交易日」的日K，避免資料庫無限長大。
    用實際存在的交易日決定，不是單純日曆天數。"""
    initialize_database()
    keep_days = max(1, int(keep_days))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT substr(bar_time, 1, 10) AS trade_date
            FROM bars_1d
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (keep_days,),
        ).fetchall()
        if len(rows) < keep_days:
            return 0
        cutoff_date = rows[-1]["trade_date"]
        cursor = connection.execute(
            "DELETE FROM bars_1d WHERE substr(bar_time, 1, 10) < ?",
            (cutoff_date,),
        )
        return int(cursor.rowcount or 0)


def daily_bars_storage_status() -> dict[str, Any]:
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS n, COUNT(DISTINCT stock_code) AS codes,
                   MIN(substr(bar_time, 1, 10)) AS first_date,
                   MAX(substr(bar_time, 1, 10)) AS last_date
            FROM bars_1d
            """
        ).fetchone()
    return {
        "barCount": int(row["n"]), "stockCount": int(row["codes"]),
        "firstTradeDate": row["first_date"], "lastTradeDate": row["last_date"],
    }
