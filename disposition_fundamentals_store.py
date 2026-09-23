"""處置股預測Phase 2所需的基本面/融資融券資料表：本益比/股價淨值比/市值(→推算發行
股數／週轉率)、融資融券餘額(→券資比)。跟main_force_store.py/broker_branch_weekly.py
一樣的_ensure_table+ON CONFLICT寫入慣例。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from database import get_connection, initialize_database


def ensure_fundamentals_schema() -> None:
    initialize_database()
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS stock_fundamentals_daily (
                stock_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                pe_ratio REAL,
                pbr REAL,
                dividend_yield REAL,
                market_value REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (stock_code, trade_date)
            );
            CREATE TABLE IF NOT EXISTS stock_margin_short_daily (
                stock_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                margin_today_balance REAL,
                margin_limit REAL,
                short_today_balance REAL,
                short_limit REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (stock_code, trade_date)
            );
            """
        )


def save_fundamentals_rows(rows: list[dict[str, Any]]) -> int:
    """rows每筆：{code, tradeDate, peRatio, pbr, dividendYield, marketValue}(缺的可以None)。"""
    ensure_fundamentals_schema()
    if not rows:
        return 0
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = [
        (
            r["code"], r["tradeDate"], r.get("peRatio"), r.get("pbr"),
            r.get("dividendYield"), r.get("marketValue"), updated_at,
        )
        for r in rows
    ]
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO stock_fundamentals_daily
                (stock_code, trade_date, pe_ratio, pbr, dividend_yield, market_value, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, trade_date) DO UPDATE SET
                pe_ratio = excluded.pe_ratio, pbr = excluded.pbr,
                dividend_yield = excluded.dividend_yield, market_value = excluded.market_value,
                updated_at = excluded.updated_at
            """,
            payload,
        )
    return len(payload)


def save_margin_short_rows(rows: list[dict[str, Any]]) -> int:
    """rows每筆：{code, tradeDate, marginTodayBalance, marginLimit, shortTodayBalance, shortLimit}。"""
    ensure_fundamentals_schema()
    if not rows:
        return 0
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = [
        (
            r["code"], r["tradeDate"], r.get("marginTodayBalance"), r.get("marginLimit"),
            r.get("shortTodayBalance"), r.get("shortLimit"), updated_at,
        )
        for r in rows
    ]
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO stock_margin_short_daily
                (stock_code, trade_date, margin_today_balance, margin_limit, short_today_balance, short_limit, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, trade_date) DO UPDATE SET
                margin_today_balance = excluded.margin_today_balance, margin_limit = excluded.margin_limit,
                short_today_balance = excluded.short_today_balance, short_limit = excluded.short_limit,
                updated_at = excluded.updated_at
            """,
            payload,
        )
    return len(payload)


def load_fundamentals_day(trade_date: str, codes: set[str] | None = None) -> dict[str, dict[str, Any]]:
    ensure_fundamentals_schema()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT stock_code, pe_ratio, pbr, dividend_yield, market_value
            FROM stock_fundamentals_daily WHERE trade_date = ?
            """,
            (trade_date,),
        ).fetchall()
    return {
        r["stock_code"]: {
            "peRatio": r["pe_ratio"], "pbr": r["pbr"],
            "dividendYield": r["dividend_yield"], "marketValue": r["market_value"],
        }
        for r in rows if codes is None or r["stock_code"] in codes
    }


def load_margin_short_day(trade_date: str, codes: set[str] | None = None) -> dict[str, dict[str, Any]]:
    ensure_fundamentals_schema()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT stock_code, margin_today_balance, margin_limit, short_today_balance, short_limit
            FROM stock_margin_short_daily WHERE trade_date = ?
            """,
            (trade_date,),
        ).fetchall()
    return {
        r["stock_code"]: {
            "marginTodayBalance": r["margin_today_balance"], "marginLimit": r["margin_limit"],
            "shortTodayBalance": r["short_today_balance"], "shortLimit": r["short_limit"],
        }
        for r in rows if codes is None or r["stock_code"] in codes
    }


def load_margin_short_range(code: str, end_date: str, days: int) -> list[dict[str, Any]]:
    """單一股票、由舊到新，最多days筆（含end_date當天，若當天已存），每筆是完整欄位
    （含限額），給算「前一營業日券資比/使用率」跟「最近6營業日最低券資比」用，不用
    再另外查一次。"""
    ensure_fundamentals_schema()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, margin_today_balance, margin_limit, short_today_balance, short_limit
            FROM stock_margin_short_daily
            WHERE stock_code = ? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT ?
            """,
            (code, end_date, days),
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))
