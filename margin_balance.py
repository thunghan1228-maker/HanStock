"""個股融資餘額（2026-10-23 使用者：融資斷頭線只看價格，要再看「那個價位附近到底還有沒有融資」）。

每天收盤後存每一檔的融資餘額（張），給融資斷頭線算：融資餘額、融資使用率、今天增減、五日增減、月高那天到今天增減。
  - 上市：證交所「融資融券彙總」（MI_MARGN，selectType=STOCK）整張表一次一天，可指定日期回補，正式站主機直接抓得到。
  - 上櫃：櫃買中心「上櫃股票融資融券餘額」（margin/balance）也是整張表一次一天，但擋 HanStock 的正式站主機；
    由 tw-groups 的排程工作流程（tpex-margin.yml，GitHub runner）每天晚上抓下來推到 data 分支 tpex/margin-stock-<日期>.json，
    後端從鏡像拉回來（跟三大法人同一套做法），日期清單在 tpex/margin-stock-index.json。
單位：張（官方原始單位）。證交所 21:00 以後才公布當天，那之前不去抓。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from chips_daily import _default_fetcher, _mirror_url
from database import get_connection, initialize_database
from trading_days import is_trading_day, previous_trading_day

logger = logging.getLogger("hanstock.margin_balance")
TW_TZ = timezone(timedelta(hours=8))

TWSE_MARGN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={ymd}&selectType=STOCK&response=json"
PUBLISH_MINUTE = 21 * 60          # 證交所融資融券彙總晚上才出來
BACKFILL_DAYS = int(os.getenv("HANSTOCK_MARGIN_BACKFILL_DAYS", "30"))   # 月高視窗 20 日＋月高那天往前，留 30 個交易日
MAX_FETCH_PER_RUN = 12            # 一輪最多抓幾天（上市），沒抓完下一輪接著
POLL_SECONDS = 20 * 60
INDEX_NAME = "margin-stock-index.json"

_started = False
_lock = threading.Lock()
_state: dict[str, Any] = {"lastRunAt": None, "lastResult": None, "lastError": None}


def _enabled() -> bool:
    return os.getenv("HANSTOCK_MARGIN_COLLECTOR_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


# ------------------------------------------------------------------ 資料表

def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS margin_daily (
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            market TEXT NOT NULL,
            margin_buy INTEGER NOT NULL,
            margin_sell INTEGER NOT NULL,
            margin_repay INTEGER NOT NULL,
            margin_prev INTEGER NOT NULL,
            margin_balance INTEGER NOT NULL,
            margin_limit INTEGER NOT NULL,
            short_balance INTEGER NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, stock_code)
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_margin_daily_code ON margin_daily (stock_code, trade_date)")


def save_margin(trade_date: str, market: str, rows: list[dict[str, Any]], source: str) -> int:
    """存一天一個市場的融資餘額；同一天同一檔重抓就覆蓋。"""
    if not rows:
        return 0
    initialize_database()
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO margin_daily (trade_date, stock_code, market, margin_buy, margin_sell, margin_repay, margin_prev, "
            "margin_balance, margin_limit, short_balance, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(trade_date, r["code"], market, r["buy"], r["sell"], r["repay"], r["prev"], r["balance"], r["limit"], r["short"], source, now) for r in rows],
        )
    return len(rows)


def has_market(trade_date: str, market: str) -> bool:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT 1 FROM margin_daily WHERE trade_date = ? AND market = ? LIMIT 1", (trade_date, market)).fetchone()
    return row is not None


def stored_dates(limit: int = 60) -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT DISTINCT trade_date FROM margin_daily ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()
    return [str(row["trade_date"]) for row in rows]


# ------------------------------------------------------------------ 解析

def _num(value: Any) -> int:
    text = str(value if value is not None else "0").strip().replace(",", "")
    if text in {"", "-", "--"}:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _iso(raw: Any) -> str | None:
    text = str(raw or "")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and text.isdigit() else None


def parse_twse_margn(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """證交所融資融券彙總（股票）：欄位名有重複（融資、融券各一組買進／賣出…），照位置取：
    2 買進、3 賣出、4 現金償還、5 前日餘額、6 今日餘額、7 次一營業日限額（融資）；12 今日餘額（融券）。
    回 (交易日, rows)；休市或還沒公布 stat 不是 OK → (None, [])。"""
    if not isinstance(payload, dict) or str(payload.get("stat") or "").upper() != "OK":
        return None, []
    table = next((t for t in payload.get("tables") or [] if t.get("data") and len(t.get("fields") or []) >= 14), None)
    if not table:
        return None, []
    fields = [str(f) for f in table["fields"]]
    if fields[0] != "代號" or "今日餘額" not in fields[6] or "限額" not in fields[7]:
        raise RuntimeError(f"MI_MARGN 欄位不符: {fields}")
    rows = []
    for row in table["data"]:
        code = str(row[0]).strip().upper() if row else ""
        if not code or len(row) < 13:      # 合計列代號是全形空白
            continue
        rows.append({"code": code, "name": str(row[1]).strip(), "buy": _num(row[2]), "sell": _num(row[3]), "repay": _num(row[4]),
                     "prev": _num(row[5]), "balance": _num(row[6]), "limit": _num(row[7]), "short": _num(row[12])})
    return _iso(payload.get("date")), rows


def parse_tpex_margin(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """櫃買中心上櫃股票融資融券餘額（也是鏡像檔的格式）：欄位名稱對，不靠位置。"""
    if not isinstance(payload, dict):
        return None, []
    table = next((t for t in payload.get("tables") or [] if t.get("data") and t.get("fields")), None)
    if not table:
        return None, []
    idx = {str(f): i for i, f in enumerate(table["fields"])}
    need = ("代號", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資限額", "券餘額")
    if any(name not in idx for name in need):
        raise RuntimeError(f"櫃買融資欄位不符: {list(idx)}")
    rows = []
    for row in table["data"]:
        code = str(row[idx["代號"]]).strip().upper() if row else ""
        if not code:
            continue
        rows.append({"code": code, "name": str(row[idx["名稱"]]).strip() if "名稱" in idx else "",
                     "buy": _num(row[idx["資買"]]), "sell": _num(row[idx["資賣"]]), "repay": _num(row[idx["現償"]]),
                     "prev": _num(row[idx["前資餘額(張)"]]), "balance": _num(row[idx["資餘額"]]), "limit": _num(row[idx["資限額"]]),
                     "short": _num(row[idx["券餘額"]])})
    return _iso(payload.get("date")), rows


# ------------------------------------------------------------------ 抓取

def fetch_twse_margn(trade_date: str, fetcher: Callable[[str], Any] | None = None) -> list[dict[str, Any]]:
    call = fetcher or _default_fetcher
    parsed_date, rows = parse_twse_margn(call(TWSE_MARGN_URL.format(ymd=trade_date.replace("-", ""))))
    if parsed_date and parsed_date != trade_date:
        raise RuntimeError(f"MI_MARGN 回的日期 {parsed_date} 不是 {trade_date}")
    return rows


def fetch_tpex_mirror_index(fetcher: Callable[[str], Any] | None = None) -> list[str]:
    """鏡像裡有哪些日子（tpex/margin-stock-index.json）；抓不到就當空的。"""
    call = fetcher or _default_fetcher
    try:
        payload = call(_mirror_url(INDEX_NAME, volatile=True))
    except Exception:  # noqa: BLE001
        return []
    return [str(d) for d in payload if isinstance(d, str)] if isinstance(payload, list) else []


def fetch_tpex_mirror_date(trade_date: str, fetcher: Callable[[str], Any] | None = None) -> list[dict[str, Any]]:
    call = fetcher or _default_fetcher
    try:
        day, rows = parse_tpex_margin(call(_mirror_url(f"margin-stock-{trade_date}.json", volatile=False)))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []
        raise
    return rows if day == trade_date else []


def _recent_trading_days(now: datetime, count: int) -> list[str]:
    today = now.date()
    days: list[date] = []
    cursor = today if (is_trading_day(today) and now.hour * 60 + now.minute >= PUBLISH_MINUTE) else previous_trading_day(today)
    while len(days) < count:
        days.append(cursor)
        cursor = previous_trading_day(cursor)
    return [d.isoformat() for d in days]


def collect_once(now: datetime | None = None, fetcher: Callable[[str], Any] | None = None, delay: float = 2.0,
                 mirror_fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """抓還沒存的日子：上市直接抓證交所（新的先抓、一輪最多 MAX_FETCH_PER_RUN 天）；上櫃問鏡像有哪些日子再一天天拉。"""
    now = now or datetime.now(TW_TZ)
    result: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "twse": [], "tpex": [], "errors": []}
    wanted = _recent_trading_days(now, BACKFILL_DAYS)
    fetched = 0
    for day in wanted:
        if has_market(day, "TSE"):
            continue
        if fetched >= MAX_FETCH_PER_RUN:
            break
        try:
            rows = fetch_twse_margn(day, fetcher)
        except Exception as error:  # noqa: BLE001
            result["errors"].append(f"TSE {day}: {type(error).__name__}: {error}"[:200])
            break  # 連不上就先停，下一輪再試
        fetched += 1
        if rows:
            save_margin(day, "TSE", rows, "twse")
            result["twse"].append({"date": day, "rows": len(rows)})
        else:
            result["twse"].append({"date": day, "rows": 0, "note": "尚未公布或休市"})
        time.sleep(delay)
    missing = [day for day in wanted if not has_market(day, "OTC")]
    available = set(fetch_tpex_mirror_index(mirror_fetcher)) if missing else set()
    for day in missing:
        if day not in available:
            continue
        try:
            rows = fetch_tpex_mirror_date(day, mirror_fetcher)
        except Exception as error:  # noqa: BLE001
            result["errors"].append(f"OTC mirror {day}: {type(error).__name__}: {error}"[:200])
            break
        if rows:
            save_margin(day, "OTC", rows, "mirror")
            result["tpex"].append({"date": day, "rows": len(rows), "source": "mirror"})
    return result


# ------------------------------------------------------------------ 查詢

def history(codes: list[str], since: str, until: str) -> dict[str, dict[str, dict[str, int]]]:
    """{代號: {日期: {balance, prev, limit, buy, sell, repay}}}，since～until（含）。"""
    out: dict[str, dict[str, dict[str, int]]] = {}
    if not codes:
        return out
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"SELECT stock_code, trade_date, margin_balance, margin_prev, margin_limit, margin_buy, margin_sell, margin_repay FROM margin_daily "
                f"WHERE stock_code IN ({','.join('?' for _ in batch)}) AND trade_date >= ? AND trade_date <= ?",
                (*batch, since, until),
            ).fetchall()
            for row in rows:
                out.setdefault(str(row["stock_code"]).upper(), {})[str(row["trade_date"])] = {
                    "balance": int(row["margin_balance"]), "prev": int(row["margin_prev"]), "limit": int(row["margin_limit"]),
                    "buy": int(row["margin_buy"]), "sell": int(row["margin_sell"]), "repay": int(row["margin_repay"])}
    return out


def _pct(now_value: float, before: float | None) -> float | None:
    return round((now_value / before - 1) * 100, 2) if before and before > 0 else None


def metrics(series: dict[str, dict[str, int]] | None, as_of: str, high_date: str | None, close: float | None) -> dict[str, Any] | None:
    """一檔的融資水位：餘額（張）、融資市值（億）、使用率（餘額÷限額）、今天增減（張／%）、五個交易日前到今天（%）、月高那天收盤餘額到今天（%）。
    那天沒有資料就回 None；前面的天數不夠對照的欄位是 None。"""
    today = (series or {}).get(as_of)
    if not today:
        return None
    dates = sorted(d for d in series if d < as_of)
    balance = today["balance"]
    prev = today["prev"] if today["prev"] else (series[dates[-1]]["balance"] if dates else None)
    five = series[dates[-5]]["balance"] if len(dates) >= 5 else None
    at_high = series[high_date]["balance"] if high_date and high_date in series and high_date < as_of else None
    return {
        "balance": balance, "limit": today["limit"],
        "valueYi": round(balance * close / 1e5, 1) if close else None,
        "util": round(balance / today["limit"] * 100, 2) if today["limit"] else None,
        "chg1": balance - prev if prev is not None else None, "chg1Pct": _pct(balance, prev),
        "chg5Pct": _pct(balance, five), "sinceHighPct": _pct(balance, at_high), "atHigh": at_high,
        "buy": today["buy"], "sell": today["sell"], "repay": today["repay"],
    }


# ------------------------------------------------------------------ 背景收集

def collector_status() -> dict[str, Any]:
    return {"enabled": _enabled(), "pollSeconds": POLL_SECONDS, "backfillDays": BACKFILL_DAYS, "dates": stored_dates(5), **_state}


def run_collect() -> dict[str, Any]:
    now = datetime.now(TW_TZ)
    try:
        result = collect_once(now)
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastResult": result, "lastError": None})
    except Exception as error:  # noqa: BLE001
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastError": f"{type(error).__name__}: {error}"[:300]})
        logger.exception("個股融資餘額收集失敗")
        result = {"error": _state["lastError"]}
    return result


def _loop() -> None:
    while True:
        run_collect()
        time.sleep(POLL_SECONDS)


def start_margin_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if not _enabled():
            logger.info("個股融資餘額收集已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-margin-balance", daemon=True).start()
        _started = True
        return True
