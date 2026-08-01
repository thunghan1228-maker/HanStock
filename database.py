import sqlite3
from pathlib import Path


DATA_DIR = Path(__file__).parent / "data"
DATABASE_PATH = DATA_DIR / "hanstock.db"


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
            """
        )

    print(f"資料庫初始化完成：{DATABASE_PATH}")


if __name__ == "__main__":
    initialize_database()