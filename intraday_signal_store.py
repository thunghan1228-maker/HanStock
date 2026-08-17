"""HanStock 盤中 5 分鐘訊號的 Railway SQLite 永久保存層。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from database import get_connection

ONCE_PER_DAY_KINDS = {
    "break905d",
    "a8short",
    "firstCrossUp20ma",
    "firstCrossDown20ma",
    "triangleNearBreakout",
    "triangleBreakoutPendingVolume",
    "triangleVolumeBreakout",
}
ONCE_PER_BAR_KINDS = {"daytradeEarlySell50", "daytradeEarlyBuy50"}
EARLY_SIGNAL_COOLDOWN_MS = 5 * 60 * 1000


def _ensure_table() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS intraday_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT NOT NULL,
                group_name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                bar_ts INTEGER NOT NULL,
                price REAL NOT NULL,
                ma20_down INTEGER,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE (trade_date, ticker, kind, bar_ts, note)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_intraday_signals_date_ts
            ON intraday_signals (trade_date, bar_ts DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_intraday_signals_ticker_date_ts
            ON intraday_signals (ticker, trade_date, bar_ts ASC, id ASC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_intraday_signals_kind_date
            ON intraday_signals (kind, trade_date, bar_ts DESC)
            """
        )


def _clean_signal(raw: dict[str, Any]) -> dict[str, Any] | None:
    try:
        trade_date = str(raw.get("tradeDate") or raw.get("trade_date") or "").strip()
        datetime.strptime(trade_date, "%Y-%m-%d")
        ticker = str(raw.get("ticker") or "").strip()[:16]
        name = str(raw.get("name") or ticker).strip()[:80]
        group_name = str(raw.get("groupName") or raw.get("group_name") or "").strip()[:80]
        kind = str(raw.get("kind") or "").strip()[:64]
        label = str(raw.get("label") or kind).strip()[:160]
        bar_ts = int(raw.get("barTs") or raw.get("bar_ts") or 0)
        price = float(raw.get("price"))
        note_raw = raw.get("note")
        note = "" if note_raw is None else str(note_raw).strip()[:160]
        ma20_raw = raw.get("ma20Down") if "ma20Down" in raw else raw.get("ma20_down")
        ma20_down = None if ma20_raw is None else (1 if bool(ma20_raw) else 0)
    except (TypeError, ValueError):
        return None
    if not ticker or not kind or bar_ts <= 0 or price <= 0:
        return None
    return {
        "trade_date": trade_date,
        "ticker": ticker,
        "name": name or ticker,
        "group_name": group_name,
        "kind": kind,
        "label": label or kind,
        "bar_ts": bar_ts,
        "price": price,
        "ma20_down": ma20_down,
        "note": note,
    }


def _to_api(row: Any) -> dict[str, Any]:
    return {
        "tradeDate": str(row["trade_date"]),
        "ticker": str(row["ticker"]),
        "name": str(row["name"]),
        "groupName": str(row["group_name"]),
        "kind": str(row["kind"]),
        "label": str(row["label"]),
        "barTs": int(row["bar_ts"]),
        "price": float(row["price"]),
        "ma20Down": None if row["ma20_down"] is None else bool(row["ma20_down"]),
        "note": str(row["note"]) if str(row["note"]) else None,
    }


def save_intraday_signals(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    _ensure_table()
    inserted: list[dict[str, Any]] = []
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_connection() as connection:
        for raw in rows:
            signal = _clean_signal(raw)
            if signal is None:
                continue
            if signal["kind"] in ONCE_PER_DAY_KINDS:
                exists = connection.execute(
                    """
                    SELECT id FROM intraday_signals
                    WHERE trade_date = ? AND ticker = ? AND kind = ?
                    LIMIT 1
                    """,
                    (signal["trade_date"], signal["ticker"], signal["kind"]),
                ).fetchone()
                if exists is not None:
                    continue
            if signal["kind"] in ONCE_PER_BAR_KINDS:
                exists = connection.execute(
                    """
                    SELECT id FROM intraday_signals
                    WHERE trade_date = ? AND ticker = ?
                      AND kind IN ('daytradeEarlySell50', 'daytradeEarlyBuy50')
                      AND bar_ts > ? AND bar_ts < ?
                    LIMIT 1
                    """,
                    (
                        signal["trade_date"], signal["ticker"],
                        signal["bar_ts"] - EARLY_SIGNAL_COOLDOWN_MS,
                        signal["bar_ts"] + EARLY_SIGNAL_COOLDOWN_MS,
                    ),
                ).fetchone()
                if exists is not None:
                    continue
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO intraday_signals (
                    trade_date, ticker, name, group_name, kind, label,
                    bar_ts, price, ma20_down, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal["trade_date"], signal["ticker"], signal["name"],
                    signal["group_name"], signal["kind"], signal["label"],
                    signal["bar_ts"], signal["price"], signal["ma20_down"],
                    signal["note"], now,
                ),
            )
            if cursor.rowcount:
                inserted.append({
                    "tradeDate": signal["trade_date"],
                    "ticker": signal["ticker"],
                    "name": signal["name"],
                    "groupName": signal["group_name"],
                    "kind": signal["kind"],
                    "label": signal["label"],
                    "barTs": signal["bar_ts"],
                    "price": signal["price"],
                    "ma20Down": None if signal["ma20_down"] is None else bool(signal["ma20_down"]),
                    "note": signal["note"] or None,
                })
    return inserted


def load_latest_signals(trade_date: str, limit: int = 20, market_only: bool = False) -> list[dict[str, Any]]:
    _ensure_table()
    limit = max(1, min(int(limit), 200))
    where = "trade_date = ?"
    params: list[Any] = [trade_date]
    if market_only:
        where += " AND kind = 'break15kLow'"
    else:
        where += " AND kind <> 'break15kLow'"
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM intraday_signals
            WHERE {where}
            ORDER BY bar_ts DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_to_api(row) for row in rows]


def load_latest_signals_by_kind(
    trade_date: str,
    kind: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _ensure_table()
    limit = max(1, min(int(limit), 500))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM intraday_signals
            WHERE trade_date = ? AND kind = ?
            ORDER BY bar_ts DESC, id DESC
            LIMIT ?
            """,
            (trade_date, kind, limit),
        ).fetchall()
    return [_to_api(row) for row in rows]


def load_signals_for_ticker(
    ticker: str,
    trade_date: str | None = None,
    since_ts: int | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    _ensure_table()
    limit = max(1, min(int(limit), 2000))
    clauses = ["ticker = ?"]
    params: list[Any] = [ticker]
    if trade_date:
        clauses.append("trade_date = ?")
        params.append(trade_date)
    if since_ts is not None:
        clauses.append("bar_ts >= ?")
        params.append(int(since_ts))
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM intraday_signals
            WHERE {' AND '.join(clauses)}
            ORDER BY bar_ts ASC, id ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_to_api(row) for row in rows]


def load_recent_trade_dates(limit: int = 10) -> list[str]:
    _ensure_table()
    limit = max(1, min(int(limit), 60))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, MAX(bar_ts) AS latest_ts
            FROM intraday_signals
            GROUP BY trade_date
            ORDER BY latest_ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [str(row["trade_date"]) for row in rows]


def purge_early_signals(trade_date: str, kind: str, cutoff_ts: int) -> int:
    _ensure_table()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM intraday_signals
            WHERE trade_date = ? AND kind = ? AND bar_ts < ?
            """,
            (trade_date, kind, int(cutoff_ts)),
        )
        return max(0, int(cursor.rowcount or 0))


def intraday_signal_count() -> int:
    _ensure_table()
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS n FROM intraday_signals").fetchone()
    return int(row["n"] if row else 0)
