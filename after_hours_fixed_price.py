"""盤後定價交易（14:00-14:30撮合，14:30公布結果）成交價/成交量。

跟 Shioaji 完全無關，不佔用即時報價訂閱額度。欄位解析方式沿用
official_daily_bars.py 已經驗證過的彈性比對邏輯（欄位標籤可能有多種
寫法），但這裡只需要代號/名稱/成交價/成交量，不需要開高低（盤後定價
本來就沒有這些欄位，是單一撮合價）。

端點：官方報表代號是BFT41U（「盤後定價交易」），路徑是/exchangeReport/
而不是/rwd/zh/afterTrading/（那是MI_INDEX等一般盤後資料用的board，
是不同的board，之前第一版寫錯路徑跟參數名稱，Railway log證實抓回來的
是空結果/JSON解析失敗）。查詢參數是selectType=ALL，不是afterTrading
board用的type=ALLBUT0999。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

from database import get_connection
from official_daily_bars import _eligible_code, _normalise_label, _number, fetch_json

UTC = timezone.utc
TWSE_AFTER_HOURS_URL = "https://www.twse.com.tw/exchangeReport/BFT41U"

FIELD_ALIASES = {
    "code": {"證券代號", "股票代號", "代號"},
    "name": {"證券名稱", "股票名稱", "名稱"},
    "price": {"成交價", "盤後定價", "定價"},
    "volume": {"成交股數", "成交量", "成交數量"},
}


def _field_indexes(fields: list[Any]) -> dict[str, int] | None:
    normalised = [_normalise_label(field) for field in fields]
    indexes: dict[str, int] = {}
    for key, aliases in FIELD_ALIASES.items():
        for index, label in enumerate(normalised):
            if label in aliases:
                indexes[key] = index
                break
    return indexes if set(indexes) == set(FIELD_ALIASES) else None


def _table_candidates(payload: dict[str, Any]) -> Iterable[tuple[list[Any], list[Any]]]:
    for table in payload.get("tables", []) or []:
        if isinstance(table, dict):
            fields = table.get("fields") or table.get("columns")
            rows = table.get("data") or table.get("rows")
            if isinstance(fields, list) and isinstance(rows, list):
                yield fields, rows
    for index in range(1, 30):
        fields = payload.get(f"fields{index}")
        rows = payload.get(f"data{index}")
        if isinstance(fields, list) and isinstance(rows, list):
            yield fields, rows
    fields = payload.get("fields")
    rows = payload.get("data")
    if isinstance(fields, list) and isinstance(rows, list):
        yield fields, rows


def _row_to_entry(row: list[Any], indexes: dict[str, int]) -> dict[str, Any] | None:
    try:
        code = str(row[indexes["code"]]).strip().upper()
        name = str(row[indexes["name"]]).strip()
    except (IndexError, TypeError):
        return None
    if not _eligible_code(code):
        return None
    price = _number(row[indexes["price"]])
    if price is None or price <= 0:
        return None
    volume = max(0, int(_number(row[indexes["volume"]]) or 0))
    if volume <= 0:
        return None
    return {"stock_code": code, "stock_name": name, "price": price, "volume": volume}


def parse_after_hours_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for fields, rows in _table_candidates(payload):
        indexes = _field_indexes(fields)
        if indexes is None:
            continue
        for row in rows:
            if not isinstance(row, list):
                continue
            entry = _row_to_entry(row, indexes)
            if entry:
                selected[entry["stock_code"]] = entry
    if not selected:
        # 欄位比對不到任何資料：可能真的還沒公布、也可能官方欄位名稱跟預期的不同。
        # 印出來源payload的鍵，方便之後對照調整 FIELD_ALIASES。
        keys = list(payload.keys()) if isinstance(payload, dict) else []
        print(f"  · 盤後定價交易空結果診斷：payload頂層鍵={keys}", flush=True)
    return [selected[code] for code in sorted(selected)]


def fetch_after_hours_day(trade_date: date) -> list[dict[str, Any]]:
    payload = fetch_json(
        TWSE_AFTER_HOURS_URL,
        {"date": trade_date.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"},
    )
    return parse_after_hours_payload(payload)


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS after_hours_fixed_price (
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            price REAL NOT NULL,
            volume INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, stock_code)
        )"""
    )


def save_after_hours_day(trade_date_str: str, entries: list[dict[str, Any]]) -> int:
    if not entries:
        return 0
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = [
        (trade_date_str, e["stock_code"], e["stock_name"], e["price"], e["volume"], updated_at)
        for e in entries
    ]
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            """INSERT INTO after_hours_fixed_price
                (trade_date, stock_code, stock_name, price, volume, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, stock_code) DO UPDATE SET
                    stock_name = excluded.stock_name,
                    price = excluded.price,
                    volume = excluded.volume,
                    updated_at = excluded.updated_at""",
            rows,
        )
    return len(rows)


def load_latest_after_hours_day(
    limit: int = 200,
    on_or_before: str | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """最近一個已收集到的盤後定價交易日（含當天）。今天 14:35 前還沒公布時，
    這會是前一個交易日；沒有任何資料時回 (None, [])。"""
    with get_connection() as connection:
        _schema(connection)
        if on_or_before:
            row = connection.execute(
                "SELECT MAX(trade_date) AS trade_date FROM after_hours_fixed_price WHERE trade_date <= ?",
                (on_or_before,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT MAX(trade_date) AS trade_date FROM after_hours_fixed_price"
            ).fetchone()
    trade_date = row["trade_date"] if row is not None else None
    if not trade_date:
        return None, []
    return trade_date, load_after_hours_day(trade_date, limit=limit)


def load_after_hours_day(trade_date_str: str, limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute(
            """SELECT stock_code, stock_name, price, volume, updated_at
               FROM after_hours_fixed_price
               WHERE trade_date = ?
               ORDER BY volume DESC
               LIMIT ?""",
            (trade_date_str, limit),
        ).fetchall()
    return [
        {
            "code": row["stock_code"],
            "name": row["stock_name"],
            "price": float(row["price"]),
            "volume": int(row["volume"]),
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]
