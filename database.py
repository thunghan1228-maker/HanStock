import sqlite3
import threading
from datetime import datetime
from typing import Iterable


from paths import DATA_DIR

DATABASE_PATH = DATA_DIR / "hanstock.db"
_journal_lock = threading.Lock()
_journal_ready_path: str | None = None

ALLOWED_BAR_TABLES = {
    "bars_1m",
    "bars_5m",
    "bars_1d",
}


class ClosingConnection(sqlite3.Connection):
    """Commit/roll back as usual, then promptly release SQLite pages and handles."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_connection() -> sqlite3.Connection:
    """建立資料庫連線。"""
    global _journal_ready_path
    DATA_DIR.mkdir(exist_ok=True)

    # 主力分鐘資料會由背景執行緒持續寫入；讀取 API 不應因短暫寫入鎖直接失敗。
    connection = sqlite3.connect(DATABASE_PATH, timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    # A large minute-bar write must not stall signal readers for 30 seconds.
    # WAL permits readers to use the last committed snapshot during writes.
    # Configure once per database, outside any application transaction.
    database_path = str(DATABASE_PATH.resolve())
    if _journal_ready_path != database_path:
        try:
            with _journal_lock:
                if _journal_ready_path != database_path:
                    mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                    if str(mode).lower() != "wal":
                        raise sqlite3.OperationalError("Unable to enable concurrent SQLite reads")
                    _journal_ready_path = database_path
        except Exception:
            connection.close()
            raise
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
