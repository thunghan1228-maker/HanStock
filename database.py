import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable


DATA_DIR = Path(__file__).parent / "data"
DATABASE_PATH = DATA_DIR / "hanstock.db"

ALLOWED_BAR_TABLES = {
    "bars_1m",
    "bars_5m",
    "bars_1d",
}


def get_connection() -> sqlite3.Connection:
    """建立資料庫連線。"""
    DATA_DIR.mkdir(exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row

    return connection


def initialize_database() -> None:
    """建立 HanStock 所需的基本資料表。"""
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS stocks (
                stock_code TEXT PRIMARY KEY,
                stock_name TEXT NOT NULL,
                market TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bars_1m (
                stock_code TEXT NOT NULL,
                bar_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                PRIMARY KEY (stock_code, bar_time)
            );

            CREATE TABLE IF NOT EXISTS bars_5m (
                stock_code TEXT NOT NULL,
                bar_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                PRIMARY KEY (stock_code, bar_time)
            );

            CREATE TABLE IF NOT EXISTS bars_1d (
                stock_code TEXT NOT NULL,
                bar_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                PRIMARY KEY (stock_code, bar_time)
            );
            """
        )


def save_stock(
    stock_code: str,
    stock_name: str,
    market: str = "TSE",
) -> None:
    """新增或更新股票基本資料。"""
    updated_at = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO stocks (
                stock_code,
                stock_name,
                market,
                updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name = excluded.stock_name,
                market = excluded.market,
                updated_at = excluded.updated_at
            """,
            (
                stock_code,
                stock_name,
                market,
                updated_at,
            ),
        )


def save_bars(
    table_name: str,
    stock_code: str,
    bars: Iterable[dict],
) -> int:
    """新增或更新一分鐘、五分鐘或日K線。"""
    if table_name not in ALLOWED_BAR_TABLES:
        raise ValueError(f"不允許的資料表：{table_name}")

    rows = []

    for bar in bars:
        bar_time = bar["time"]

        if not isinstance(bar_time, datetime):
            raise TypeError("K線時間必須是 datetime 格式。")

        rows.append(
            (
                stock_code,
                bar_time.isoformat(),
                float(bar["open"]),
                float(bar["high"]),
                float(bar["low"]),
                float(bar["close"]),
                int(bar["volume"]),
            )
        )

    with get_connection() as connection:
        connection.executemany(
            f"""
            INSERT INTO {table_name} (
                stock_code,
                bar_time,
                open,
                high,
                low,
                close,
                volume
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, bar_time) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume
            """,
            rows,
        )

    return len(rows)


if __name__ == "__main__":
    initialize_database()
    print(f"資料庫初始化完成：{DATABASE_PATH}")