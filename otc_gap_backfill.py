"""一次性回補：TPEx舊端點401/403期間造成的OTC(上櫃)日K歷史缺口。

official_daily_bars.py已經改用官方OpenAPI快照修正「今天起」的收集，
但那個快照只回傳最新一個交易日，沒辦法回補already缺掉的歷史區間。
這裡改用FinMind TaiwanStockPrice（這個repo已經在付費使用的Sponsor
資料源，見finmind_active_etf_flow.py／finmind_broker_branch_collector.py，
不是新串接的服務）一次拿回缺口區間內每個交易日的全市場收盤資料。

FinMind一次回傳的是全市場（上市+上櫃+興櫃混在一起），所以只挑「這個
repo自己資料庫裡已經記錄是OTC」的代號寫回去；其餘（包含所有上市股票）
一律忽略，避免_save_day把stocks.market欄位誤覆蓋成OTC。

只在第一次成功、沒有任何失敗的整段回補後才標記完成；之後開機不會
重跑。_save_day本身是ON CONFLICT DO NOTHING，所以就算標記失敗、下次
開機重跑整段區間，已經成功寫入的日子也不會被覆蓋或重複計入。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from database import get_connection, initialize_database
from official_daily_bars import _save_day

logger = logging.getLogger("hanstock.otc_gap_backfill")
UTC = timezone.utc
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
PRICE_DATASET = "TaiwanStockPrice"

# Railway production log證實TPEx舊端點從這一天起持續401/403。
GAP_START = date(2026, 7, 21)

_started = False
_lock = threading.Lock()


def _enabled() -> bool:
    return os.getenv(
        "HANSTOCK_OTC_GAP_BACKFILL_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}


def _token() -> str:
    return os.getenv("FINMIND_TOKEN", "").strip()


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS otc_gap_backfill_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            done INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            updated_at TEXT NOT NULL
        )"""
    )


def backfill_state() -> dict[str, Any]:
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute(
            "SELECT done, result_json, updated_at FROM otc_gap_backfill_state WHERE id = 1"
        ).fetchone()
    if row is None:
        return {"done": False, "result": None, "updatedAt": None}
    return {
        "done": bool(row["done"]),
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "updatedAt": row["updated_at"],
    }


def _mark_state(done: bool, result: dict[str, Any]) -> None:
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.execute(
            """INSERT INTO otc_gap_backfill_state (id, done, result_json, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    done = excluded.done,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at""",
            (1 if done else 0, json.dumps(result, ensure_ascii=False), updated_at),
        )


def _known_otc_codes(connection) -> set[str]:
    rows = connection.execute("SELECT stock_code FROM stocks WHERE market = 'OTC'").fetchall()
    return {row["stock_code"] for row in rows}


def _known_stock_names(connection) -> dict[str, str]:
    rows = connection.execute("SELECT stock_code, stock_name FROM stocks").fetchall()
    return {row["stock_code"]: row["stock_name"] for row in rows}


def _default_fetcher(url: str, params: dict[str, str]) -> dict[str, Any]:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": "HanStock/1.0 (+https://hanstock.xyz)",
        },
    )
    with urlopen(request, timeout=45) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def fetch_finmind_price_day(
    trade_date: date, *, fetcher: Callable[..., Any] | None = None
) -> list[dict[str, Any]]:
    """沒有token是刻意跳過，回傳[]；但實際呼叫失敗要往外拋，讓
    backfill_otc_gap能分辨「這天真的沒交易」跟「這天沒抓到、要重試」，
    否則_run_once會把因為網路問題整段沒抓到的回補誤標記成done。"""
    token = _token()
    if not token:
        return []
    day = trade_date.isoformat()
    call = fetcher or _default_fetcher
    payload = call(
        FINMIND_DATA_URL,
        {"dataset": PRICE_DATASET, "start_date": day, "end_date": day, "token": token},
    )
    rows = payload.get("data") if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def _row_to_otc_bar(
    entry: dict[str, Any],
    trade_date: date,
    known_codes: set[str],
    names: dict[str, str],
) -> dict[str, Any] | None:
    code = str(entry.get("stock_id") or "").strip().upper()
    if code not in known_codes:
        # 只回補已經記錄是OTC的代號；FinMind一次回傳全市場，混雜上市股票，
        # 絕對不能連market欄位一起覆蓋，否則會把上市股票錯標成上櫃。
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
        # FinMind的Trading_Volume單位是股，換算成跟官方日K/Hub一致的「張」。
        volume = max(0, int(float(entry.get("Trading_Volume") or 0) / 1000))
    except (TypeError, ValueError):
        volume = 0
    return {
        "stock_code": code,
        "stock_name": names.get(code, code),
        "market": "OTC",
        "time": datetime.combine(trade_date, datetime_time.min, tzinfo=UTC),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _weekdays(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def backfill_otc_gap(
    start: date = GAP_START,
    end: date | None = None,
    *,
    delay: float = 0.3,
    fetcher: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """回補start~end（含）之間、已知OTC代號的日K缺口。_save_day是ON CONFLICT
    DO NOTHING，已經有的bar不會被覆蓋，重複呼叫、範圍重疊都是安全的。"""
    initialize_database()
    end = end or date.today()
    with get_connection() as connection:
        known_codes = _known_otc_codes(connection)
        names = _known_stock_names(connection)
    days = list(_weekdays(start, end))
    inserted = 0
    days_with_data = 0
    failures: list[dict[str, str]] = []
    for trade_date in days:
        try:
            raw_rows = fetch_finmind_price_day(trade_date, fetcher=fetcher)
        except Exception as error:  # noqa: BLE001
            failures.append({"date": trade_date.isoformat(), "error": str(error)})
            time.sleep(max(0.0, delay))
            continue
        bars = [
            bar for bar in (
                _row_to_otc_bar(entry, trade_date, known_codes, names)
                for entry in raw_rows if isinstance(entry, dict)
            ) if bar
        ]
        if bars:
            days_with_data += 1
            inserted += _save_day(bars)
        time.sleep(max(0.0, delay))
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "requestedDays": len(days),
        "daysWithData": days_with_data,
        "insertedBars": inserted,
        "knownOtcCodeCount": len(known_codes),
        "failures": failures,
    }


def _run_once() -> None:
    if backfill_state()["done"]:
        return
    if not _token():
        logger.info("FINMIND_TOKEN未設定，暫緩OTC歷史缺口回補")
        return
    try:
        result = backfill_otc_gap()
    except Exception:  # noqa: BLE001
        logger.exception("OTC歷史缺口回補失敗")
        return
    # 沒有任何失敗的日子才視為完成；有失敗就保留done=False，下次開機重試
    # 整段區間（已成功的日子重跑也不會壞資料，見backfill_otc_gap docstring）。
    _mark_state(not result["failures"], result)
    logger.info("OTC歷史缺口回補: %s", result)


def start_otc_gap_backfill() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if not _enabled():
            logger.info("OTC歷史缺口回補已停用")
            return False
        threading.Thread(target=_run_once, name="hanstock-otc-gap-backfill", daemon=True).start()
        _started = True
        return True
