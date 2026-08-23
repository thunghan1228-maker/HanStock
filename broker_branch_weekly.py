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
        normalized.append(
            {
                "ticker": ticker,
                "tradeDate": trade_date,
                "netAmount": _number(item.get("netAmount", item.get("net_amount")), "netAmount"),
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
                trade_date, ticker, net_amount, concentration,
                active_branches, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, ticker) DO UPDATE SET
                net_amount = excluded.net_amount,
                concentration = excluded.concentration,
                active_branches = excluded.active_branches,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            [
                (
                    row["tradeDate"], row["ticker"], row["netAmount"],
                    row["concentration"], row["activeBranches"], row["source"], now,
                )
                for row in rows
            ],
        )
    return len(rows)


def read_latest_broker_branch_weekly() -> dict[str, Any]:
    """以最近五個实际有資料的交易日彙總，且只回傳五日完整股票。"""
    ensure_broker_branch_schema()
    with get_connection() as connection:
        dates = [
            row["trade_date"]
            for row in connection.execute(
                "SELECT DISTINCT trade_date FROM broker_branch_daily ORDER BY trade_date DESC LIMIT 5"
            ).fetchall()
        ]
        if len(dates) < 5:
            return {"weekEndDate": max(dates).replace("-", "/") if dates else None, "tradeDates": sorted(dates), "rows": [], "complete": False}
        placeholders = ",".join("?" for _ in dates)
        result = connection.execute(
            f"""
            SELECT ticker,
                   SUM(net_amount) AS net_amount,
                   AVG(concentration) AS concentration,
                   ROUND(AVG(active_branches)) AS active_branches
            FROM broker_branch_daily
            WHERE trade_date IN ({placeholders})
            GROUP BY ticker
            HAVING COUNT(DISTINCT trade_date) = 5
            ORDER BY ticker ASC
            """,
            dates,
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
            "SELECT COUNT(*) AS row_count, COUNT(DISTINCT trade_date) AS trade_days, MAX(trade_date) AS latest_date FROM broker_branch_daily"
        ).fetchone()
    return {
        "rowCount": int(row["row_count"] or 0),
        "tradeDays": int(row["trade_days"] or 0),
        "latestDate": row["latest_date"],
        "weeklyReady": int(row["trade_days"] or 0) >= 5,
    }
