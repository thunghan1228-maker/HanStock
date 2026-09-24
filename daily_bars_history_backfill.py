"""族群個股日K歷史回補：讓 MA240（均線分數）算得出來。

正式環境 bars_1d 從 2025-09-16 起有資料，但中間缺很多天（官方歷史來源有些日子沒抓到），
每檔平均只有約 181 根，醞釀／發動、盤中333 的均線分數（要 MA240）一檔都算不出來。
這裡跟 otc_gap_backfill.py 一樣用 FinMind TaiwanStockPrice「單日全市場」查詢（一天一個請求，
這個 repo 已經在付費使用的 Sponsor 資料源），把「族群個股覆蓋率不到 9 成」的交易日補齊。

- 只寫 43 個族群＋股期標的清單裡的個股（醞釀／發動、盤中333、創高黑龍都只看這些），
  而且只寫 stocks 表裡已經有紀錄的代號，股名／市場用原本的值，_save_day 更新 stocks 時不會改錯市場。
- _save_day 是 ON CONFLICT DO NOTHING：已經有的日K不覆蓋，重跑、範圍重疊都安全。
- 整段沒有失敗才標記完成，之後開機不重跑；有失敗就下次開機再補（已補的日子會被覆蓋率判斷跳過）。
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database
from official_daily_bars import _save_day
from otc_gap_backfill import fetch_finmind_price_day
from stock_groups import STOCK_GROUPS

logger = logging.getLogger("hanstock.daily_bars_history_backfill")
UTC = timezone.utc
LOOKBACK_CALENDAR_DAYS = 400
MIN_COVERAGE = 0.9

_started = False
_lock = threading.Lock()
_progress: dict[str, Any] = {"running": False, "doneDays": 0, "totalDays": 0}


def _enabled() -> bool:
    return os.getenv("HANSTOCK_GROUP_HISTORY_BACKFILL_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _token() -> str:
    return os.getenv("FINMIND_TOKEN", "").strip()


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS group_history_backfill_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            done INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            updated_at TEXT NOT NULL
        )"""
    )


def backfill_state() -> dict[str, Any]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute(
            "SELECT done, result_json, updated_at FROM group_history_backfill_state WHERE id = 1"
        ).fetchone()
    state = {"done": False, "result": None, "updatedAt": None}
    if row is not None:
        state = {
            "done": bool(row["done"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "updatedAt": row["updated_at"],
        }
    state["progress"] = dict(_progress)
    return state


def _mark_state(done: bool, result: dict[str, Any]) -> None:
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.execute(
            """INSERT INTO group_history_backfill_state (id, done, result_json, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    done = excluded.done, result_json = excluded.result_json, updated_at = excluded.updated_at""",
            (1 if done else 0, json.dumps(result, ensure_ascii=False), updated_at),
        )


def group_codes() -> list[str]:
    return sorted({str(code).strip().upper() for members in STOCK_GROUPS.values() for code, _name in members})


def _known_stocks(codes: list[str]) -> dict[str, tuple[str, str]]:
    """{代號: (股名, 市場)}，只取 stocks 表裡已經有的。"""
    out: dict[str, tuple[str, str]] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"SELECT stock_code, stock_name, market FROM stocks WHERE stock_code IN ({placeholders})", tuple(batch)
            ).fetchall()
            for row in rows:
                if row["market"]:
                    out[str(row["stock_code"]).strip().upper()] = (str(row["stock_name"] or row["stock_code"]), str(row["market"]))
    return out


def _coverage_by_date(codes: list[str], since: str, until: str) -> dict[str, int]:
    """{日期: 那天有日K的族群個股數}。"""
    counts: dict[str, int] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"""
                SELECT substr(bar_time, 1, 10) AS d, COUNT(DISTINCT stock_code) AS n FROM bars_1d
                WHERE stock_code IN ({placeholders}) AND substr(bar_time, 1, 10) >= ? AND substr(bar_time, 1, 10) <= ?
                GROUP BY substr(bar_time, 1, 10)
                """,
                (*batch, since, until),
            ).fetchall()
            for row in rows:
                counts[str(row["d"])] = counts.get(str(row["d"]), 0) + int(row["n"])
    return counts


def _weekdays(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def _row_to_bar(entry: dict[str, Any], trade_date: date, known: dict[str, tuple[str, str]]) -> dict[str, Any] | None:
    code = str(entry.get("stock_id") or "").strip().upper()
    if code not in known:
        return None
    try:
        open_ = float(entry["open"])
        high = float(entry["max"])
        low = float(entry["min"])
        close = float(entry["close"])
    except (TypeError, ValueError, KeyError):
        return None
    if min(open_, high, low, close) <= 0:
        return None
    try:
        volume = max(0, int(float(entry.get("Trading_Volume") or 0) / 1000))  # 股 → 張，跟官方日K一致
    except (TypeError, ValueError):
        volume = 0
    name, market = known[code]
    return {
        "stock_code": code, "stock_name": name, "market": market,
        "time": datetime.combine(trade_date, datetime_time.min, tzinfo=UTC),
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }


def backfill_group_history(
    *, today: date | None = None, delay: float = 0.3, fetcher: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    initialize_database()
    today = today or date.today()
    since = today - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    until = today - timedelta(days=1)
    codes = group_codes()
    known = _known_stocks(codes)
    target = max(1, math.ceil(len(known) * MIN_COVERAGE))
    coverage = _coverage_by_date(sorted(known), since.isoformat(), until.isoformat())
    days = [d for d in _weekdays(since, until) if coverage.get(d.isoformat(), 0) < target]
    _progress.update({"running": True, "doneDays": 0, "totalDays": len(days)})
    inserted = 0
    days_with_data = 0
    failures: list[dict[str, str]] = []
    try:
        for trade_date in days:
            try:
                raw_rows = fetch_finmind_price_day(trade_date, fetcher=fetcher)
            except Exception as error:  # noqa: BLE001
                failures.append({"date": trade_date.isoformat(), "error": str(error)[:200]})
                time.sleep(max(0.0, delay))
                continue
            bars = [bar for bar in (_row_to_bar(e, trade_date, known) for e in raw_rows if isinstance(e, dict)) if bar]
            if bars:
                days_with_data += 1
                inserted += _save_day(bars)
            _progress["doneDays"] += 1
            time.sleep(max(0.0, delay))
    finally:
        _progress["running"] = False
    return {
        "startDate": since.isoformat(), "endDate": until.isoformat(),
        "groupCodeCount": len(codes), "knownCodeCount": len(known), "coverageTarget": target,
        "requestedDays": len(days), "daysWithData": days_with_data, "insertedBars": inserted,
        "failures": failures,
    }


def _run_once() -> None:
    if backfill_state()["done"]:
        return
    if not _token():
        logger.info("FINMIND_TOKEN未設定，暫緩族群個股日K歷史回補")
        return
    try:
        result = backfill_group_history()
    except Exception:  # noqa: BLE001
        logger.exception("族群個股日K歷史回補失敗")
        return
    _mark_state(not result["failures"], result)
    logger.info("族群個股日K歷史回補: %s", result)
    try:
        from brew_launch import clear_cache

        clear_cache()  # 日K補齊了，醞釀／發動馬上重算，不用等半小時快取過期
    except Exception:  # noqa: BLE001
        logger.exception("清醞釀／發動快取失敗")


def start_group_history_backfill() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if not _enabled():
            logger.info("族群個股日K歷史回補已停用")
            return False
        threading.Thread(target=_run_once, name="hanstock-group-history-backfill", daemon=True).start()
        _started = True
        return True
