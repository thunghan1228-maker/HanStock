"""個股 1 分／5 分主力進出副圖的 Railway SQLite 永久保存層。"""

from __future__ import annotations

from datetime import datetime
import threading
from typing import Any, Iterable

import database
from otc_index import taipei_trade_date

_table_lock = threading.Lock()
_table_ready_path: str | None = None


def _ensure_table() -> None:
    global _table_ready_path
    database_path = str(database.DATABASE_PATH.resolve())
    if _table_ready_path == database_path:
        return
    with _table_lock:
        if _table_ready_path == database_path:
            return
        with database.get_connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS main_force_bars (
                    stock_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    bar_ts INTEGER NOT NULL,
                    main_buy_volume INTEGER NOT NULL DEFAULT 0,
                    main_sell_volume INTEGER NOT NULL DEFAULT 0,
                    main_net_volume INTEGER NOT NULL DEFAULT 0,
                    main_buy_amount REAL NOT NULL DEFAULT 0,
                    main_sell_amount REAL NOT NULL DEFAULT 0,
                    main_net_amount REAL NOT NULL DEFAULT 0,
                    main_tick_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (stock_code, interval, bar_ts)
                );
                CREATE INDEX IF NOT EXISTS idx_main_force_code_interval_date
                ON main_force_bars (stock_code, interval, trade_date, bar_ts);
                """
            )
        _table_ready_path = database_path


def _rows_for_bars(
    stock_code: str,
    interval: str,
    bars: Iterable[dict[str, Any]],
    now: str,
) -> list[tuple[Any, ...]]:
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    code = str(stock_code).strip().upper()
    rows: list[tuple[Any, ...]] = []
    for bar in bars:
        if not isinstance(bar, dict) or not bar.get("main_force_available"):
            continue
        try:
            ts = int(bar["ts"])
            buy_volume = max(0, int(bar.get("main_buy_volume", 0) or 0))
            sell_volume = max(0, int(bar.get("main_sell_volume", 0) or 0))
            buy_amount = max(0.0, float(bar.get("main_buy_amount", 0) or 0))
            sell_amount = max(0.0, float(bar.get("main_sell_amount", 0) or 0))
            tick_count = max(0, int(bar.get("main_tick_count", 0) or 0))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if ts <= 0:
            continue
        rows.append((
            code, taipei_trade_date(ts), interval, ts,
            buy_volume, sell_volume, buy_volume - sell_volume,
            buy_amount, sell_amount, buy_amount - sell_amount,
            tick_count, now,
        ))
    return rows


def _write_rows(rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    _ensure_table()
    with database.get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO main_force_bars (
                stock_code, trade_date, interval, bar_ts,
                main_buy_volume, main_sell_volume, main_net_volume,
                main_buy_amount, main_sell_amount, main_net_amount,
                main_tick_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, interval, bar_ts) DO UPDATE SET
                trade_date=excluded.trade_date,
                main_buy_volume=excluded.main_buy_volume,
                main_sell_volume=excluded.main_sell_volume,
                main_net_volume=excluded.main_net_volume,
                main_buy_amount=excluded.main_buy_amount,
                main_sell_amount=excluded.main_sell_amount,
                main_net_amount=excluded.main_net_amount,
                main_tick_count=excluded.main_tick_count,
                updated_at=excluded.updated_at
            """,
            rows,
        )
    return len(rows)


def save_main_force_bars(stock_code: str, interval: str, bars: Iterable[dict[str, Any]]) -> int:
    """只保存真正含有主力逐筆統計的 K 棒；不以零值偽造缺漏資料。"""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return _write_rows(_rows_for_bars(stock_code, interval, bars, now))


def save_main_force_batches(
    entries: Iterable[tuple[str, str, Iterable[dict[str, Any]]]],
) -> int:
    """在單一交易中批次保存多檔股票，避免每分鐘建立上千個 SQLite 寫入交易。"""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    rows: list[tuple[Any, ...]] = []
    for stock_code, interval, bars in entries:
        rows.extend(_rows_for_bars(stock_code, interval, bars, now))
    return _write_rows(rows)


def load_main_force_bars(
    stock_code: str,
    interval: str,
    *,
    trade_date: str | None = None,
    days: int = 31,
    limit: int = 20000,
) -> list[dict[str, Any]]:
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    code = str(stock_code).strip().upper()
    params: list[Any] = [code, interval]
    if trade_date:
        date_filter = "AND trade_date = ?"
        params.append(trade_date)
    else:
        date_filter = ""
    with database.get_connection() as connection:
        if not trade_date:
            recent_dates = connection.execute(
                """
                SELECT trade_date
                FROM main_force_bars
                WHERE stock_code = ? AND interval = ?
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT ?
                """,
                (code, interval, max(1, min(days, 400))),
            ).fetchall()
            if not recent_dates:
                return []
            date_filter = "AND trade_date >= ?"
            params = [code, interval, recent_dates[-1]["trade_date"]]
        row_limit = max(1, min(limit, 100000))
        params.append(row_limit)
        rows = connection.execute(
            f"""
            SELECT trade_date, bar_ts, main_buy_volume, main_sell_volume,
                   main_net_volume, main_buy_amount, main_sell_amount,
                   main_net_amount, main_tick_count
            FROM main_force_bars
            WHERE stock_code = ? AND interval = ? {date_filter}
            ORDER BY bar_ts DESC LIMIT ?
            """,
            params,
        ).fetchall()
    rows = list(reversed(rows))
    return [{
        "trade_date": row["trade_date"], "ts": int(row["bar_ts"]),
        "main_buy_volume": int(row["main_buy_volume"]),
        "main_sell_volume": int(row["main_sell_volume"]),
        "main_net_volume": int(row["main_net_volume"]),
        "main_buy_amount": round(float(row["main_buy_amount"])),
        "main_sell_amount": round(float(row["main_sell_amount"])),
        "main_net_amount": round(float(row["main_net_amount"])),
        "main_tick_count": int(row["main_tick_count"]),
        "main_force_available": True,
    } for row in rows]


def main_force_storage_status() -> dict[str, Any]:
    _ensure_table()
    with database.get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT stock_code) AS codes, COUNT(DISTINCT trade_date) AS dates, MIN(trade_date) AS first_date, MAX(trade_date) AS last_date FROM main_force_bars"
        ).fetchone()
    return {
        "barCount": int(row["n"]), "stockCount": int(row["codes"]),
        "tradeDateCount": int(row["dates"]), "firstTradeDate": row["first_date"],
        "lastTradeDate": row["last_date"],
    }
