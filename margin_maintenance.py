"""大盤融資維持率（2026-11-21 使用者：首頁融資那一塊最上面加大盤的融資維持率，每天更新）。

大盤融資維持率＝所有融資股票的市值 ÷ 融資借款 × 100%：
  - 市值：證交所「融資融券彙總」（MI_MARGN）每一檔的融資今日餘額（張）× 1000 × 那天收盤價（MI_INDEX），全部加起來；
  - 融資借款：同一份 MI_MARGN 的「信用交易統計」裡「融資金額（仟元）今日餘額」。
只算上市（新聞講的大盤融資維持率就是上市）。130% 是追繳線（個股維持率低於 130% 收追繳令）。
證交所 21:00 以後才公布當天融資，那之前不算當天；第一次跑回補最近 BACKFILL_DAYS 個交易日，畫走勢用。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from chips_daily import _default_fetcher
from database import get_connection, initialize_database
from trading_days import is_trading_day, previous_trading_day

logger = logging.getLogger("hanstock.margin_maintenance")
TW_TZ = timezone(timedelta(hours=8))

MARGN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={ymd}&selectType=ALL&response=json"
QUOTES_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json"
PUBLISH_MINUTE = 21 * 60
BACKFILL_DAYS = int(os.getenv("HANSTOCK_MARGIN_RATIO_BACKFILL_DAYS", "20"))
POLL_SECONDS = 20 * 60

_started = False
_lock = threading.Lock()
_state: dict[str, Any] = {"lastRunAt": None, "lastResult": None, "lastError": None}


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS margin_maintenance_daily (
            trade_date TEXT PRIMARY KEY,
            ratio REAL NOT NULL,
            margin_amount REAL NOT NULL,
            market_value REAL NOT NULL,
            margin_lots INTEGER,
            priced INTEGER,
            unpriced INTEGER,
            updated_at TEXT NOT NULL
        )"""
    )


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _fields(table: dict[str, Any]) -> list[str]:
    return [str(f).replace("\n", "").replace(" ", "") for f in table.get("fields") or []]


def parse_margin(payload: Any) -> tuple[float | None, dict[str, float]]:
    """(融資金額（元）, {代號: 融資今日餘額（張）})；沒有資料（還沒公布、休市）回 (None, {})。"""
    if not isinstance(payload, dict):
        return None, {}
    amount, balances = None, {}
    for table in payload.get("tables") or []:
        fields = _fields(table)
        if fields[:1] == ["項目"] and "今日餘額" in fields:
            col = fields.index("今日餘額")
            for row in table.get("data") or []:
                if row and str(row[0]).startswith("融資金額"):
                    value = _num(row[col])
                    amount = value * 1000 if value is not None else None   # 仟元
        elif fields[:1] == ["代號"] and "今日餘額" in fields:
            col = fields.index("今日餘額")   # 第一個「今日餘額」是融資（後面還有融券的）
            for row in table.get("data") or []:
                lots = _num(row[col]) if len(row) > col else None
                if row and lots:
                    balances[str(row[0]).strip().upper()] = lots
    return amount, balances


def parse_closes(payload: Any) -> dict[str, float]:
    closes: dict[str, float] = {}
    if not isinstance(payload, dict):
        return closes
    for table in payload.get("tables") or []:
        fields = _fields(table)
        if "證券代號" in fields and "收盤價" in fields:
            code_col, close_col = fields.index("證券代號"), fields.index("收盤價")
            for row in table.get("data") or []:
                close = _num(row[close_col]) if len(row) > close_col else None
                if close:
                    closes[str(row[code_col]).strip().upper()] = close
    return closes


def compute(trade_date: str, fetcher: Callable[[str], Any] | None = None, delay: float = 1.5) -> dict[str, Any] | None:
    """某一天的大盤融資維持率；證交所那天還沒公布回 None。"""
    call = fetcher or _default_fetcher
    ymd = trade_date.replace("-", "")
    amount, balances = parse_margin(call(MARGN_URL.format(ymd=ymd)))
    if not amount or not balances:
        return None
    time.sleep(delay)
    closes = parse_closes(call(QUOTES_URL.format(ymd=ymd)))
    if not closes:
        return None
    value, priced, unpriced = 0.0, 0, 0
    for code, lots in balances.items():
        close = closes.get(code)
        if close:
            value += lots * 1000 * close
            priced += 1
        else:
            unpriced += 1   # 停牌、全額交割沒收盤價的，市值算不到（通常只有幾檔、幾千張）
    return {"date": trade_date, "ratio": round(value / amount * 100, 2), "marginAmount": amount, "marketValue": value,
            "marginLots": int(sum(balances.values())), "priced": priced, "unpriced": unpriced}


def save(row: dict[str, Any]) -> None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.execute(
            """INSERT OR REPLACE INTO margin_maintenance_daily
               (trade_date, ratio, margin_amount, market_value, margin_lots, priced, unpriced, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["date"], row["ratio"], row["marginAmount"], row["marketValue"], row["marginLots"], row["priced"], row["unpriced"],
             datetime.now(TW_TZ).isoformat(timespec="seconds")),
        )


def stored_dates() -> set[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        return {str(r["trade_date"]) for r in connection.execute("SELECT trade_date FROM margin_maintenance_daily").fetchall()}


def _recent_trading_days(now: datetime, count: int) -> list[str]:
    today = now.date()
    cursor = today if (is_trading_day(today) and now.hour * 60 + now.minute >= PUBLISH_MINUTE) else previous_trading_day(today)
    days = []
    while len(days) < count:
        days.append(cursor.isoformat())
        cursor = previous_trading_day(cursor)
    return days


def collect_once(now: datetime | None = None, fetcher: Callable[[str], Any] | None = None, delay: float = 1.5) -> dict[str, Any]:
    now = now or datetime.now(TW_TZ)
    have = stored_dates()
    result: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "added": [], "pending": [], "errors": []}
    for day in _recent_trading_days(now, BACKFILL_DAYS):
        if day in have:
            continue
        try:
            row = compute(day, fetcher, delay)
        except Exception as error:  # noqa: BLE001
            result["errors"].append(f"{day}: {type(error).__name__}: {error}"[:200])
            break   # 連不上就先停，下一輪再試
        if row:
            save(row)
            result["added"].append({"date": day, "ratio": row["ratio"]})
        else:
            result["pending"].append(day)
        time.sleep(delay)
    return result


def payload(days: int = 20) -> dict[str, Any]:
    """首頁用：最新的大盤融資維持率、比前一天差多少、最近幾天的走勢（新到舊）。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute(
            "SELECT trade_date, ratio, margin_amount, market_value, margin_lots FROM margin_maintenance_daily ORDER BY trade_date DESC LIMIT ?",
            (max(2, min(120, int(days))),),
        ).fetchall()
    series = [{"date": str(r["trade_date"]), "ratio": float(r["ratio"]), "marginAmount": round(float(r["margin_amount"]) / 1e8, 1),
               "marketValue": round(float(r["market_value"]) / 1e8, 1), "marginLots": r["margin_lots"]} for r in rows]
    if not series:
        return {"status": "empty", "message": "還沒有大盤融資維持率資料", "series": []}
    latest = dict(series[0])
    if len(series) > 1:
        latest["change"] = round(series[0]["ratio"] - series[1]["ratio"], 2)
        latest["amountChange"] = round(series[0]["marginAmount"] - series[1]["marginAmount"], 1)
    return {"status": "ok", "market": "TSE", "latest": latest, "series": series, "marginCallLine": 130, **{k: _state.get(k) for k in ("lastRunAt",)}}


def collector_status() -> dict[str, Any]:
    return {"pollSeconds": POLL_SECONDS, "backfillDays": BACKFILL_DAYS, **_state}


def run_collect() -> dict[str, Any]:
    now = datetime.now(TW_TZ)
    try:
        result = collect_once(now)
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastResult": result, "lastError": None})
    except Exception as error:  # noqa: BLE001
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastError": f"{type(error).__name__}: {error}"[:300]})
        logger.exception("大盤融資維持率收集失敗")
        result = {"error": _state["lastError"]}
    return result


def _loop() -> None:
    while True:
        run_collect()
        time.sleep(POLL_SECONDS)


def start_margin_maintenance_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_MARGIN_RATIO_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return False
        threading.Thread(target=_loop, name="hanstock-margin-maintenance", daemon=True).start()
        _started = True
        return True
