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


def bar_codes(min_bars: int = 21) -> list[str]:
    """有 min_bars 根以上日K的代號（族群表內加盤中訊號追蹤的全市場股票）；基本面收集與持股健診的範圍。"""
    initialize_database()
    with get_connection() as connection:
        rows = connection.execute("SELECT stock_code, COUNT(*) AS n FROM bars_1d GROUP BY stock_code HAVING n >= ?", (max(1, int(min_bars)),)).fetchall()
    return sorted({str(r["stock_code"]).strip().upper() for r in rows})


def recent_bar_dates(limit: int, *, before: str | None = None, include_before: bool = False) -> list[str]:
    """日K表裡最近 limit 個有資料的交易日（新到舊）；before＝只看這天之前（include_before＝含這天）。
    沿著 bar_time 索引一天一天往回跳（每步只查一次 MAX），不用 SELECT DISTINCT 把整張表掃過。"""
    limit = int(limit)
    if limit <= 0:
        return []
    initialize_database()
    upper = "\uffff" if before is None else (f"{str(before)[:10]}z" if include_before else str(before)[:10])
    with get_connection() as connection:
        rows = connection.execute(
            """
            WITH RECURSIVE days(d) AS (
                SELECT substr(MAX(bar_time), 1, 10) FROM bars_1d WHERE bar_time < ?
                UNION ALL
                SELECT (SELECT substr(MAX(bar_time), 1, 10) FROM bars_1d WHERE bar_time < days.d)
                FROM days WHERE days.d IS NOT NULL
            )
            SELECT d FROM days WHERE d IS NOT NULL LIMIT ?
            """,
            (upper, limit),
        ).fetchall()
    return [str(row[0]) for row in rows]


def latest_daily_trade_date_before(trade_date: str) -> str | None:
    """全市場日K裡、早於 trade_date 的最新交易日（YYYY-MM-DD）。個股的「昨日」日K比這個日期舊，
    就代表那檔的日K沒跟上（例如上櫃來源被擋），不能拿來當昨高／昨收。"""
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT substr(MAX(bar_time), 1, 10) AS d FROM bars_1d WHERE bar_time < ?",
            (str(trade_date)[:10],),
        ).fetchone()
    return str(row["d"]) if row and row["d"] else None


def prune_old_daily_bars(keep_days: int = 365) -> int:
    """只保留最近 keep_days 個「有資料的交易日」的日K，避免資料庫無限長大。
    用實際存在的交易日決定，不是單純日曆天數。"""
    initialize_database()
    keep_days = max(1, int(keep_days))
    dates = recent_bar_dates(keep_days)
    if len(dates) < keep_days:
        return 0
    cutoff_date = dates[-1]
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM bars_1d WHERE bar_time < ?",
            (cutoff_date,),
        )
        return int(cursor.rowcount or 0)


def daily_bars_storage_status() -> dict[str, Any]:
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS n, COUNT(DISTINCT stock_code) AS codes,
                   substr(MIN(bar_time), 1, 10) AS first_date,
                   substr(MAX(bar_time), 1, 10) AS last_date
            FROM bars_1d
            """
        ).fetchone()
    return {
        "barCount": int(row["n"]), "stockCount": int(row["codes"]),
        "firstTradeDate": row["first_date"], "lastTradeDate": row["last_date"],
    }
