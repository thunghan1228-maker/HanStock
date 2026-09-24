"""族群個股日K歷史回補：讓 MA240（均線分數）算得出來。

正式環境 bars_1d 從 2025-09-16 起有資料，但中間缺很多天（官方歷史來源有些日子沒抓到），
每檔平均只有約 181 根，醞釀／發動、盤中333 的均線分數（要 MA240）一檔都算不出來；
創高黑龍的均線分數在本地日K不夠時也是退回 FinMind 逐檔抓。

FinMind 的付費方案 2026-09-23 到期：單日全市場查詢回 HTTP 400、逐檔查詢回 402 Payment
Required，連續打了 391 檔之後連 IP 都被暫時封鎖（403 ip banned）。改成：

- 主要來源：Yahoo chart API 日K（interval=1d，免費、不用 token；正式環境確認連得到），
  43 個族群裡日K不足 245 根的股票，一檔一個請求抓今天往前 400 天。
- 備援：FinMind 逐檔（有 token 才試）；只要回一次錯誤（402／403／429…）這一輪就不再打，
  不要再把 IP 打到被封。
- 只寫本地沒有的日子（_save_day 是 ON CONFLICT DO NOTHING），官方日K優先保留；
  寫之前拿重疊日子的收盤價跟官方日K對一下，價格對不上（例如 Yahoo 做了減資／分割調整）
  就整檔不寫，免得兩種價格基準拼在一起把均線算歪。
- 沒成交（量 0）的日子不算一根，跟官方日K一致（官方停牌日沒有日K）。
- 只寫 43 個族群裡、而且 stocks 表已經有紀錄的代號，股名／市場用原本的值，_save_day 更新
  stocks 時不會改錯市場。
- 整段沒有失敗才標記完成，之後開機不重跑；有失敗就下次開機再補（已經補夠的股票會被根數判斷跳過）。
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
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from database import get_connection, initialize_database
from history_sources import YAHOO_CHART_URL, _tw_epoch, _yahoo_symbols
from official_daily_bars import _save_day
from otc_index import taipei_trade_date
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

logger = logging.getLogger("hanstock.daily_bars_history_backfill")
UTC = timezone.utc
LOOKBACK_CALENDAR_DAYS = 400
MIN_BARS = 245  # MA240 要 240 根，多留一點
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
PRICE_DATASET = "TaiwanStockPrice"
MAX_STORED_FAILURES = 20
MAX_STORED_CODES = 60
YAHOO_RETRY_DELAY_SECONDS = 3.0
# 重疊日子（本地已經有官方日K的那幾天）收盤價差超過 1% 算對不上；超過一成的重疊日對不上就整檔不寫。
PRICE_MATCH_TOLERANCE = 0.01
PRICE_MISMATCH_MAX_RATIO = 0.1

_started = False
_lock = threading.Lock()
_progress: dict[str, Any] = {"running": False, "doneStocks": 0, "totalStocks": 0}


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
    return sorted({
        str(code).strip().upper()
        for name, members in STOCK_GROUPS.items() if name not in SPECIAL_GROUP_NAMES
        for code, _name in members
    })


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


def _bar_counts(codes: list[str], since: str, until: str) -> dict[str, int]:
    """{代號: since～until 之間已經有的日K根數}。"""
    counts: dict[str, int] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"""
                SELECT stock_code, COUNT(*) AS n FROM bars_1d
                WHERE stock_code IN ({placeholders}) AND substr(bar_time, 1, 10) >= ? AND substr(bar_time, 1, 10) <= ?
                GROUP BY stock_code
                """,
                (*batch, since, until),
            ).fetchall()
            for row in rows:
                counts[str(row["stock_code"]).strip().upper()] = int(row["n"])
    return counts


def _existing_closes(code: str, since: str, until: str) -> dict[str, float]:
    """{日期: 收盤價}：本地（官方）已經有的日K，用來核對外部來源的價格基準。"""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT substr(bar_time, 1, 10) AS d, close FROM bars_1d
            WHERE stock_code = ? AND substr(bar_time, 1, 10) >= ? AND substr(bar_time, 1, 10) <= ?
            """,
            (code, since, until),
        ).fetchall()
    return {str(row["d"]): float(row["close"]) for row in rows}


def _default_fetcher(url: str, params: dict[str, str]) -> dict[str, Any]:
    request = Request(
        f"{url}?{urlencode(params)}",
        # 跟 history_sources 的 Yahoo 分K用同一個 User-Agent（正式環境確認 Yahoo 接受）。
        headers={"Accept": "application/json,text/plain,*/*", "User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)"},
    )
    with urlopen(request, timeout=45) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or number <= 0:
        return None
    return number


def _day_bar(open_: Any, high: Any, low: Any, close: Any, volume_shares: Any, factor: float = 1.0) -> dict[str, Any] | None:
    """一天的 OHLCV；價格乘 factor 還原成當天實際價（量除 factor），量從股換成張。沒成交或欄位缺回 None。"""
    prices = [_positive(value) for value in (open_, high, low, close)]
    if any(value is None for value in prices):
        return None
    volume = int((_positive(volume_shares) or 0) / factor / 1000)  # 股 → 張，跟官方日K一致
    if volume <= 0:
        return None  # 沒成交（停牌）：官方日K那天也沒有這檔，不算一根
    o, h, l, c = (round(value * factor, 2) for value in prices)  # type: ignore[operator]
    return {"open": o, "high": max(h, o, c), "low": min(l, o, c), "close": c, "volume": volume}


def _split_factors(result: dict[str, Any]) -> list[tuple[str, float]]:
    """Yahoo 的 close 有做分割調整（台股配股也常被記成分割）：[(除權日, 分子/分母)]。
    除權日之前的價格乘回這個比例，才是當天實際成交價、跟官方日K同一個基準。"""
    splits = ((result.get("events") or {}).get("splits") or {}).values()
    out: list[tuple[str, float]] = []
    for split in splits:
        try:
            ratio = float(split["numerator"]) / float(split["denominator"])
            day = taipei_trade_date(int(split["date"]) * 1000)
        except (TypeError, ValueError, KeyError, ZeroDivisionError, OverflowError):
            continue
        if ratio > 0 and ratio != 1:
            out.append((day, ratio))
    return out


def parse_yahoo_daily(payload: Any, since: str, until: str) -> dict[str, dict[str, Any]]:
    """Yahoo chart（interval=1d）→ {台北日期: {open, high, low, close, volume(張)}}，已還原分割調整。"""
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        return {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo chart error: {chart['error']}")
    result = (chart.get("result") or [None])[0] or {}
    timestamps = result.get("timestamp") or []
    block = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    columns = [block.get(key) or [] for key in ("open", "high", "low", "close", "volume")]
    splits = _split_factors(result)
    days: dict[str, dict[str, Any]] = {}
    for index, ts_sec in enumerate(timestamps):
        try:
            day = taipei_trade_date(int(ts_sec) * 1000)
        except (TypeError, ValueError, OverflowError):
            continue
        if not since <= day <= until:
            continue
        factor = 1.0
        for split_day, ratio in splits:
            if day < split_day:
                factor *= ratio
        bar = _day_bar(*(column[index] if index < len(column) else None for column in columns), factor=factor)
        if bar:
            days[day] = bar
    return days


def fetch_yahoo_daily(
    code: str, market: str | None, since: str, until: str, *, fetcher: Callable[..., Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """一檔股票 since～until 的 Yahoo 日K；市場別決定 .TW／.TWO（不確定兩個都試）。呼叫失敗往外拋。"""
    call = fetcher or _default_fetcher
    last_error: Exception | None = None
    for symbol in _yahoo_symbols(code, market):
        try:
            payload = call(YAHOO_CHART_URL + quote(symbol, safe=""), {
                "interval": "1d", "period1": str(_tw_epoch(since)), "period2": str(_tw_epoch(until, 23, 59, 59)),
                "events": "split", "includePrePost": "false",
            })
            days = parse_yahoo_daily(payload, since, until)
        except Exception as error:  # noqa: BLE001
            last_error = error
            continue
        if days:
            return days
    if last_error is not None:
        raise last_error
    return {}


def fetch_stock_history(code: str, since: str, until: str, *, fetcher: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    """一檔股票 since～until 的 FinMind 日K（備援）；沒有 token 回 []；呼叫失敗往外拋。"""
    token = _token()
    if not token:
        return []
    payload = (fetcher or _default_fetcher)(
        FINMIND_DATA_URL,
        {"dataset": PRICE_DATASET, "data_id": code, "start_date": since, "end_date": until, "token": token},
    )
    rows = payload.get("data") if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def _finmind_days(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {}
    for entry in rows:
        if not isinstance(entry, dict) or not entry.get("date"):
            continue
        bar = _day_bar(entry.get("open"), entry.get("max"), entry.get("min"), entry.get("close"), entry.get("Trading_Volume"))
        if bar:
            days[str(entry["date"])[:10]] = bar
    return days


def _prices_match(days: dict[str, dict[str, Any]], existing: dict[str, float]) -> bool:
    """外部來源跟本地官方日K重疊的日子，收盤價要對得上（容許少數幾天有出入）。"""
    overlap = [day for day in days if existing.get(day, 0) > 0]
    if not overlap:
        return True
    off = sum(1 for day in overlap if abs(days[day]["close"] / existing[day] - 1) > PRICE_MATCH_TOLERANCE)
    return off <= len(overlap) * PRICE_MISMATCH_MAX_RATIO


def backfill_group_history(
    *, today: date | None = None, delay: float = 0.4, retry_delay: float = YAHOO_RETRY_DELAY_SECONDS,
    fetcher: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    initialize_database()
    today = today or date.today()
    since = (today - timedelta(days=LOOKBACK_CALENDAR_DAYS)).isoformat()
    until = (today - timedelta(days=1)).isoformat()
    codes = group_codes()
    known = _known_stocks(codes)
    counts = _bar_counts(sorted(known), since, until)
    targets = [code for code in sorted(known) if counts.get(code, 0) < MIN_BARS]
    _progress.update({"running": True, "doneStocks": 0, "totalStocks": len(targets)})
    finmind_usable = bool(_token())
    finmind_error: str | None = None
    inserted = 0
    stocks_with_data = 0
    source_counts = {"yahoo": 0, "finmind": 0}
    mismatched: list[str] = []
    failures: list[dict[str, str]] = []
    try:
        for code in targets:
            name, market = known[code]
            days: dict[str, dict[str, Any]] = {}
            source = "yahoo"
            error: Exception | None = None
            for attempt in range(2):  # Yahoo 偶爾 429／逾時，隔幾秒再試一次
                try:
                    days = fetch_yahoo_daily(code, market, since, until, fetcher=fetcher)
                    error = None
                    break
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    if attempt == 0:
                        time.sleep(max(0.0, retry_delay))
            if not days and finmind_usable:
                try:
                    days = _finmind_days(fetch_stock_history(code, since, until, fetcher=fetcher))
                    if days:
                        source, error = "finmind", None
                except Exception as exc:  # noqa: BLE001
                    # 402（方案到期）／403（IP 被封）／429：這一輪不再打 FinMind，免得越打越久被封
                    finmind_usable = False
                    finmind_error = str(exc)[:200]
                    error = error or exc
            _progress["doneStocks"] += 1
            if error is not None:
                failures.append({"code": code, "error": str(error)[:200]})
                time.sleep(max(0.0, delay))
                continue
            existing = _existing_closes(code, since, until)
            if not _prices_match(days, existing):
                mismatched.append(code)  # 價格基準對不上（減資／分割調整不同），整檔不寫
                time.sleep(max(0.0, delay))
                continue
            bars = [
                {
                    "stock_code": code, "stock_name": name, "market": market,
                    "time": datetime.combine(date.fromisoformat(day), datetime_time.min, tzinfo=UTC), **bar,
                }
                for day, bar in sorted(days.items()) if day not in existing
            ]
            if days:
                stocks_with_data += 1
                source_counts[source] += 1
            if bars:
                inserted += _save_day(bars)
            time.sleep(max(0.0, delay))
    finally:
        _progress["running"] = False
    after = _bar_counts(targets, since, until) if targets else {}
    still_short = [code for code in targets if after.get(code, 0) < MIN_BARS]
    return {
        "mode": "per_stock", "source": "yahoo", "startDate": since, "endDate": until,
        "groupCodeCount": len(codes), "knownCodeCount": len(known), "minBars": MIN_BARS,
        "requestedStocks": len(targets), "stocksWithData": stocks_with_data, "insertedBars": inserted,
        "sourceCounts": source_counts, "finmindError": finmind_error,
        "mismatchCount": len(mismatched), "mismatched": mismatched[:MAX_STORED_CODES],
        # 補完還是不到 MIN_BARS 的（上市未滿一年、長期停牌）：醞釀／發動會列在 insufficient，不是失敗
        "stillShortCount": len(still_short), "stillShort": still_short[:MAX_STORED_CODES],
        "failureCount": len(failures), "failures": failures[:MAX_STORED_FAILURES],
    }


def _run_once() -> None:
    if backfill_state()["done"]:
        return
    try:
        result = backfill_group_history()
    except Exception:  # noqa: BLE001
        logger.exception("族群個股日K歷史回補失敗")
        return
    _mark_state(not result.get("failureCount", len(result.get("failures") or [])), result)
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
