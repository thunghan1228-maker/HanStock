"""處置股預測Phase 3所需的當日沖銷成交量／借券賣出成交量資料表：第十三款(當日沖銷
比例，決定處置期間5天/7天)跟第十二款(借券賣出比例)各自只需要一個每日數字，比Phase 2
的disposition_fundamentals_store.py簡單——不需要限額欄位，因為clause 12/13的判定
都是「量/總成交量」或「跟自己60日均量比」，不涉及使用率這種需要限額的計算。

有存到row代表collector那天真的有跑且拿到FinMind回應(值可能是0，即「有出現目標名單/
報表查詢但當天沒有當沖或借券賣出」)；沒有row代表沒收集過(本功能上線前的日期，或那天
FinMind那個資料集抓取失敗)——disposition_phase3_assembly.py靠這個區別判斷視窗天數
夠不夠，不會把"沒收集過"誤當成"當天是0"。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from database import get_connection, initialize_database


def ensure_phase3_schema() -> None:
    initialize_database()
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS stock_day_trading_daily (
                stock_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                day_trading_volume REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (stock_code, trade_date)
            );
            CREATE TABLE IF NOT EXISTS stock_sbl_short_sale_daily (
                stock_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                sbl_short_sale_volume REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (stock_code, trade_date)
            );
            """
        )


def save_day_trading_rows(rows: list[dict[str, Any]]) -> int:
    """rows每筆：{code, tradeDate, volume}。"""
    ensure_phase3_schema()
    if not rows:
        return 0
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = [(r["code"], r["tradeDate"], r["volume"], updated_at) for r in rows]
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO stock_day_trading_daily (stock_code, trade_date, day_trading_volume, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_code, trade_date) DO UPDATE SET
                day_trading_volume = excluded.day_trading_volume, updated_at = excluded.updated_at
            """,
            payload,
        )
    return len(payload)


def save_sbl_short_sale_rows(rows: list[dict[str, Any]]) -> int:
    """rows每筆：{code, tradeDate, volume}。"""
    ensure_phase3_schema()
    if not rows:
        return 0
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = [(r["code"], r["tradeDate"], r["volume"], updated_at) for r in rows]
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO stock_sbl_short_sale_daily (stock_code, trade_date, sbl_short_sale_volume, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_code, trade_date) DO UPDATE SET
                sbl_short_sale_volume = excluded.sbl_short_sale_volume, updated_at = excluded.updated_at
            """,
            payload,
        )
    return len(payload)


def load_day_trading_range(code: str, end_date: str, days: int) -> list[dict[str, Any]]:
    """單一股票、由舊到新，最多days筆(含end_date當天，若當天已存)。"""
    ensure_phase3_schema()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, day_trading_volume FROM stock_day_trading_daily
            WHERE stock_code = ? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT ?
            """,
            (code, end_date, days),
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def load_sbl_short_sale_range(code: str, end_date: str, days: int) -> list[dict[str, Any]]:
    """單一股票、由舊到新，最多days筆(含end_date當天，若當天已存)。"""
    ensure_phase3_schema()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, sbl_short_sale_volume FROM stock_sbl_short_sale_daily
            WHERE stock_code = ? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT ?
            """,
            (code, end_date, days),
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))
