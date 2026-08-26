"""正式券商分點每日寫入與最近五个交易日週彙總。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from database import get_connection


def ensure_broker_branch_schema() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS broker_branch_daily (
                trade_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                net_amount REAL NOT NULL,
                net_lots REAL,
                concentration REAL NOT NULL,
                active_branches INTEGER NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (trade_date, ticker)
            );
            CREATE INDEX IF NOT EXISTS broker_branch_daily_ticker_date_idx
                ON broker_branch_daily (ticker, trade_date DESC);
            """
        )
        columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(broker_branch_daily)").fetchall()}
        if "net_lots" not in columns:
            connection.execute("ALTER TABLE broker_branch_daily ADD COLUMN net_lots REAL")


def _number(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} 必須是數字") from error


def normalize_daily_rows(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        ticker = str(item.get("ticker") or item.get("code") or "").strip().upper()
        trade_date = str(item.get("tradeDate") or item.get("trade_date") or "").strip().replace("/", "-")
        if not ticker or len(ticker) > 12 or not ticker.replace("-", "").isalnum():
            raise ValueError("ticker 格式不正確")
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("tradeDate 必須是 YYYY-MM-DD") from error
        active_branches = int(_number(item.get("activeBranches", item.get("active_branches", 0)), "activeBranches"))
        raw_net_lots = item.get("netLots", item.get("net_lots"))
        normalized.append(
            {
                "ticker": ticker,
                "tradeDate": trade_date,
                "netAmount": _number(item.get("netAmount", item.get("net_amount")), "netAmount"),
                "netLots": None if raw_net_lots is None else _number(raw_net_lots, "netLots"),
                "concentration": _number(item.get("concentration"), "concentration"),
                "activeBranches": max(0, active_branches),
                "source": str(item.get("source") or "official-broker-branch").strip(),
            }
        )
    return normalized


def save_broker_branch_daily(rows: list[dict[str, Any]]) -> int:
    ensure_broker_branch_schema()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO broker_branch_daily (
                trade_date, ticker, net_amount, net_lots, concentration,
                active_branches, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, ticker) DO UPDATE SET
                net_amount = excluded.net_amount,
                net_lots = excluded.net_lots,
                concentration = excluded.concentration,
                active_branches = excluded.active_branches,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            [
                (
                    row["tradeDate"], row["ticker"], row["netAmount"],
                    row["netLots"], row["concentration"], row["activeBranches"], row["source"], now,
                )
                for row in rows
            ],
        )
    return len(rows)


def read_latest_broker_branch_daily() -> dict[str, Any]:
    """回傳最新同一公式來源的完整單日分點彙總，不混用較舊來源。"""
    ensure_broker_branch_schema()
    with get_connection() as connection:
        latest_row = connection.execute(
            """
            SELECT trade_date, source, MAX(updated_at) AS latest_update
            FROM broker_branch_daily
            GROUP BY trade_date, source
            HAVING SUM(CASE WHEN active_branches > 0 THEN 1 ELSE 0 END) > 0
            ORDER BY trade_date DESC, latest_update DESC
            LIMIT 1
            """
        ).fetchone()
        if not latest_row:
            return {"tradeDate": None, "rows": [], "complete": False}
        trade_date = str(latest_row["trade_date"])
        source = str(latest_row["source"])
        result = connection.execute(
            """
            SELECT ticker, net_amount, net_lots, concentration, active_branches
            FROM broker_branch_daily
            WHERE trade_date = ? AND source = ?
            ORDER BY ticker ASC
            """,
            (trade_date, source),
        ).fetchall()
    display_date = trade_date.replace("-", "/")
    return {
        "tradeDate": display_date,
        "complete": bool(result),
        "rows": [
            {
                "ticker": row["ticker"],
                "tradeDate": display_date,
                "netAmount": round(float(row["net_amount"]), 2),
                "netLots": round(float(row["net_lots"]), 3) if row["net_lots"] is not None else None,
                "concentration": round(float(row["concentration"]), 4),
                "activeBranches": int(row["active_branches"] or 0),
            }
            for row in result
        ],
    }


def read_latest_broker_branch_weekly() -> dict[str, Any]:
    """以同一公式來源最近五個交易日彙總，且只回傳五日完整股票。"""
    ensure_broker_branch_schema()
    with get_connection() as connection:
        latest_source_row = connection.execute(
            """
            SELECT source, MAX(updated_at) AS latest_update
            FROM broker_branch_daily
            GROUP BY source
            HAVING SUM(CASE WHEN active_branches > 0 THEN 1 ELSE 0 END) > 0
            ORDER BY latest_update DESC
            LIMIT 1
            """
        ).fetchone()
        latest_source = str(latest_source_row["source"]) if latest_source_row else None
        dates = [
            row["trade_date"]
            for row in connection.execute(
                """
                SELECT trade_date
                FROM broker_branch_daily
                WHERE source = ?
                GROUP BY trade_date
                HAVING SUM(CASE WHEN active_branches > 0 THEN 1 ELSE 0 END) > 0
                ORDER BY trade_date DESC
                LIMIT 5
                """,
                (latest_source,),
            ).fetchall()
        ] if latest_source else []
        if len(dates) < 5:
            return {"weekEndDate": max(dates).replace("-", "/") if dates else None, "tradeDates": sorted(dates), "rows": [], "complete": False}
        placeholders = ",".join("?" for _ in dates)
        result = connection.execute(
            f"""
            SELECT ticker,
                   SUM(net_amount) AS net_amount,
                   CASE WHEN COUNT(net_lots) = COUNT(*) THEN SUM(net_lots) END AS net_lots,
                   AVG(concentration) AS concentration,
                   ROUND(AVG(active_branches)) AS active_branches
            FROM broker_branch_daily
            WHERE trade_date IN ({placeholders}) AND source = ?
            GROUP BY ticker
            HAVING COUNT(DISTINCT trade_date) = 5
            ORDER BY ticker ASC
            """,
            [*dates, latest_source],
        ).fetchall()
    week_end_date = max(dates).replace("-", "/")
    return {
        "weekEndDate": week_end_date,
        "tradeDates": sorted(dates),
        "complete": True,
        "rows": [
            {
                "ticker": row["ticker"],
                "weekEndDate": week_end_date,
                "netAmount": round(float(row["net_amount"]), 2),
                "netLots": round(float(row["net_lots"]), 3) if row["net_lots"] is not None else None,
                "concentration": round(float(row["concentration"]), 4),
                "activeBranches": int(row["active_branches"] or 0),
            }
            for row in result
        ],
    }


def broker_branch_storage_status() -> dict[str, Any]:
    ensure_broker_branch_schema()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS row_count,
                   COUNT(DISTINCT trade_date) AS trade_days,
                   MAX(CASE WHEN active_branches > 0 THEN trade_date END) AS latest_date
            FROM broker_branch_daily
            """
        ).fetchone()
    return {
        "rowCount": int(row["row_count"] or 0),
        "tradeDays": int(row["trade_days"] or 0),
        "latestDate": row["latest_date"],
        "weeklyReady": int(row["trade_days"] or 0) >= 5,
    }


def stored_broker_branch_dates(limit: int = 40, source: str | None = None, *, require_net_lots: bool = False) -> list[str]:
    ensure_broker_branch_schema()
    lots_having = " AND COUNT(net_lots) = COUNT(*)" if require_net_lots else ""
    with get_connection() as connection:
        if source:
            rows = connection.execute(
                f"""
                SELECT trade_date FROM broker_branch_daily
                WHERE source = ?
                GROUP BY trade_date
                HAVING SUM(CASE WHEN active_branches > 0 THEN 1 ELSE 0 END) > 0
                    {lots_having}
                ORDER BY trade_date DESC LIMIT ?
                """,
                (source, max(1, int(limit))),
            ).fetchall()
        else:
            rows = connection.execute(
                f"""
                SELECT trade_date FROM broker_branch_daily
                GROUP BY trade_date
                HAVING SUM(CASE WHEN active_branches > 0 THEN 1 ELSE 0 END) > 0
                    {lots_having}
                ORDER BY trade_date DESC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
    return [str(row["trade_date"]) for row in rows]
