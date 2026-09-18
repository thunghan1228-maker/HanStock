"""個股 1 分／5 分主力進出副圖的 Railway SQLite 永久保存層。"""

from __future__ import annotations

from datetime import datetime
import threading
from typing import Any, Iterable

import database
from otc_index import is_regular_otc_session, taipei_trade_date

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
        if not is_regular_otc_session(ts):
            # main_force_bars 只代表盤中連續交易（09:00-13:30）逐筆主力統計；
            # 盤後定價撮合（14:00-14:30）若被上游誤判成一根新K棒，不能混進來，
            # 否則MAX(bar_ts)會被撮合時間蓋掉，看起來像「只有盤後那筆資料」。
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


def load_main_force_ranking(
    trade_date: str,
    interval: str = "5m",
    limit: int = 30,
) -> list[dict[str, Any]]:
    """依交易日彙總主力累計買賣超排行；只讀取既有已收集資料，不新增任何即時訂閱。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    limit = max(1, min(int(limit), 200))
    with database.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT b.stock_code,
                   SUM(b.main_net_volume) AS net_volume,
                   SUM(b.main_buy_volume) AS buy_volume,
                   SUM(b.main_sell_volume) AS sell_volume,
                   MAX(b.bar_ts) AS last_ts,
                   s.stock_name AS stock_name
            FROM main_force_bars b
            LEFT JOIN stocks s ON s.stock_code = b.stock_code
            WHERE b.trade_date = ? AND b.interval = ?
            GROUP BY b.stock_code
            ORDER BY ABS(SUM(b.main_net_volume)) DESC
            LIMIT ?
            """,
            (trade_date, interval, limit),
        ).fetchall()
    return [{
        "code": row["stock_code"],
        "name": row["stock_name"] or row["stock_code"],
        "netVolume": int(row["net_volume"] or 0),
        "buyVolume": int(row["buy_volume"] or 0),
        "sellVolume": int(row["sell_volume"] or 0),
        "lastTs": int(row["last_ts"]),
        "side": "buy" if (row["net_volume"] or 0) >= 0 else "sell",
    } for row in rows]


def load_daily_main_force_net(stock_code: str, interval: str = "5m") -> dict[str, int]:
    """單一股票依交易日彙總主力淨量，給日線圖副圖用。只涵蓋目前保留天數內
    （prune_old_bars 預設30個交易日）的資料，比這個範圍舊的交易日不會有值，
    是資料保留政策造成的預期限制，不是bug。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, SUM(main_net_volume) AS net_volume
            FROM main_force_bars
            WHERE stock_code = ? AND interval = ?
            GROUP BY trade_date
            """,
            (stock_code, interval),
        ).fetchall()
    return {row["trade_date"]: int(row["net_volume"] or 0) for row in rows}


def load_daily_main_force_net_amount(stock_code: str, interval: str = "5m") -> dict[str, float]:
    """跟 load_daily_main_force_net 一樣依交易日彙總，但回傳金額（元）
    而不是張數；供需要金額門檻（例如「前日大單淨額 > 1 億元」）的訊號
    使用。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, SUM(main_net_amount) AS net_amount
            FROM main_force_bars
            WHERE stock_code = ? AND interval = ?
            GROUP BY trade_date
            """,
            (stock_code, interval),
        ).fetchall()
    return {row["trade_date"]: float(row["net_amount"] or 0) for row in rows}


def previous_trade_date_with_data(stock_code: str, before_date: str, interval: str = "5m") -> str | None:
    """回傳這檔股票在 before_date 之前，最近一個有主力副圖資料的交易日；
    沒有更早資料時回 None。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    with database.get_connection() as connection:
        row = connection.execute(
            """
            SELECT trade_date FROM main_force_bars
            WHERE stock_code = ? AND interval = ? AND trade_date < ?
            ORDER BY trade_date DESC LIMIT 1
            """,
            (stock_code, interval, before_date),
        ).fetchone()
    return row["trade_date"] if row else None


def list_tracked_stock_codes(trade_date: str, interval: str = "1m") -> list[str]:
    """今日已有主力副圖資料的股票代號；用來找「目前實際在追蹤」的股票，
    不需要另外掃描或訂閱。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute(
            "SELECT DISTINCT stock_code FROM main_force_bars WHERE trade_date = ? AND interval = ?",
            (trade_date, interval),
        ).fetchall()
    return sorted(str(row["stock_code"]) for row in rows)


def prune_old_bars(keep_days: int = 30) -> int:
    """只保留最近 keep_days 個「有資料的交易日」，刪掉更早的1分/5分主力副圖，
    避免資料庫無限長大。用實際存在的 trade_date 決定，不是單純的日曆天數，
    所以會自動跳過假日。"""
    _ensure_table()
    keep_days = max(1, int(keep_days))
    with database.get_connection() as connection:
        rows = connection.execute(
            "SELECT DISTINCT trade_date FROM main_force_bars ORDER BY trade_date DESC LIMIT ?",
            (keep_days,),
        ).fetchall()
        if len(rows) < keep_days:
            return 0  # 資料還不滿 keep_days 天，還不用清
        cutoff_date = rows[-1]["trade_date"]
        cursor = connection.execute(
            "DELETE FROM main_force_bars WHERE trade_date < ?",
            (cutoff_date,),
        )
        return int(cursor.rowcount or 0)


def purge_out_of_session_bars() -> int:
    """一次性清理：收集器過去沒有限制09:00-13:30盤中連續交易時段，若上游把
    盤後定價撮合（14:00-14:30）誤判成一根K棒，就會被存進來，讓排行的
    MAX(bar_ts)顯示成盤後時間，蓋掉真正的盤中最後一筆。_rows_for_bars
    已經擋掉新資料，這裡是清掉舊資料庫裡已經寫進去的髒資料，不用等
    30天保留期自然淘汰。之後每次應該都刪0筆（冪等）。"""
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute("SELECT DISTINCT bar_ts FROM main_force_bars").fetchall()
        bad_ts = [int(row["bar_ts"]) for row in rows if not is_regular_otc_session(row["bar_ts"])]
        if not bad_ts:
            return 0
        placeholders = ",".join("?" for _ in bad_ts)
        cursor = connection.execute(
            f"DELETE FROM main_force_bars WHERE bar_ts IN ({placeholders})", bad_ts
        )
        return int(cursor.rowcount or 0)


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
