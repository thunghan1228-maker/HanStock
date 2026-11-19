"""現股當沖排行（2026-11-19 使用者：首頁「週轉率高」那欄加切換「現股當沖」，照券商 App「現股當沖個股排行榜」：
最近一個收盤日的熱門當沖標的，照當沖成交張數排，旁邊寫當沖占比%）。

- 上市：證交所「當日沖銷交易標的及成交量值」（TWTB4U，可指定日期），正式站主機直接抓。
- 上櫃：櫃買中心「現股當沖交易統計」（intraday/stat）擋我們的主機，跟上櫃三大法人一樣由 tw-groups 的排程工作流程
  抓下來推到 data 分支（tpex/daytrade-YYYY-MM-DD.json），後端再拉；每一輪也先試一次直接抓最新那天。
- 當沖占比＝當沖成交股數 ÷ 那天總成交股數（官方日K bars_1d）。
收盤後晚上才公布，每 15 分鐘看一次最近幾個交易日還沒存的。單位：資料庫存「股」，端點回「張」。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database
from trading_days import is_trading_day, previous_trading_day

logger = logging.getLogger("hanstock.daytrade_rank")
TW_TZ = timezone(timedelta(hours=8))

TWSE_URL = "https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date={ymd}&selectType=All&response=json"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/intraday/stat?type=Daily&date={slash}&response=json"
TPEX_MIRROR_BASE = os.getenv("HANSTOCK_TPEX_MIRROR_BASE", "https://raw.githubusercontent.com/thunghan1228-maker/tw-groups/data/tpex")
PUBLISH_MINUTE = 16 * 60          # 收盤後晚上才公布；16:00 前不去問當天的
BACKFILL_DAYS = 5
POLL_SECONDS = 15 * 60
MAX_TOP = 50

_started = False
_lock = threading.Lock()
_state: dict[str, Any] = {"lastRunAt": None, "lastResult": None, "lastError": None}


def _enabled() -> bool:
    return os.getenv("HANSTOCK_DAYTRADE_COLLECTOR_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


# ------------------------------------------------------------------ 資料表

def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS daytrade_daily (
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            market TEXT NOT NULL,
            stock_name TEXT,
            dt_shares INTEGER NOT NULL,
            buy_amount REAL,
            sell_amount REAL,
            suspend TEXT,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, stock_code)
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_daytrade_daily_date ON daytrade_daily (trade_date)")


def save_daytrade(trade_date: str, market: str, rows: list[dict[str, Any]], source: str) -> int:
    if not rows:
        return 0
    initialize_database()
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            """INSERT OR REPLACE INTO daytrade_daily (trade_date, stock_code, market, stock_name, dt_shares, buy_amount, sell_amount, suspend, source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(trade_date, r["code"], market, r["name"], r["shares"], r["buy"], r["sell"], r["suspend"], source, now) for r in rows],
        )
    return len(rows)


def has_market(trade_date: str, market: str) -> bool:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT 1 FROM daytrade_daily WHERE trade_date = ? AND market = ? LIMIT 1", (trade_date, market)).fetchone()
    return row is not None


def stored_dates(limit: int = 10) -> list[dict[str, Any]]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute(
            "SELECT trade_date, market, COUNT(*) AS n FROM daytrade_daily GROUP BY trade_date, market ORDER BY trade_date DESC LIMIT ?",
            (limit * 2,),
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = out.setdefault(str(row["trade_date"]), {"date": str(row["trade_date"]), "markets": {}})
        entry["markets"][str(row["market"])] = int(row["n"])
    return list(out.values())[:limit]


# ------------------------------------------------------------------ 抓資料（證交所、櫃買中心同一種格式）

def _num(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "").strip() or 0))
    except ValueError:
        return 0


def _ymd(value: Any) -> str | None:
    text = str(value or "").strip()
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and text.isdigit() else None


def parse_daytrade(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """回 (交易日, rows)；還沒公布（只有標的清單、沒有成交股數欄）就回空。"""
    if not isinstance(payload, dict):
        return None, []
    day = _ymd(payload.get("date"))
    for table in payload.get("tables") or []:
        if not isinstance(table, dict):
            continue
        fields = [str(f).replace("\n", "").replace(" ", "") for f in table.get("fields") or []]
        if "證券代號" not in fields or "當日沖銷交易成交股數" not in fields:
            continue
        col = {name: fields.index(name) for name in fields}
        flag = next((i for i, name in enumerate(fields) if "註記" in name), None)
        rows = []
        for item in table.get("data") or []:
            if not isinstance(item, list) or len(item) < len(fields):
                continue
            code = str(item[col["證券代號"]]).strip().upper()
            shares = _num(item[col["當日沖銷交易成交股數"]])
            if not code or shares <= 0:
                continue
            rows.append({
                "code": code, "name": str(item[col["證券名稱"]]).strip() if "證券名稱" in col else code, "shares": shares,
                "buy": _num(item[col["當日沖銷交易買進成交金額"]]) if "當日沖銷交易買進成交金額" in col else None,
                "sell": _num(item[col["當日沖銷交易賣出成交金額"]]) if "當日沖銷交易賣出成交金額" in col else None,
                "suspend": str(item[flag]).strip() if flag is not None else "",
            })
        return day, rows
    return day, []


def _default_fetcher(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={
        "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-TW,zh;q=0.9",
        "User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)",
        "Referer": "https://www.tpex.org.tw/" if "tpex" in url else "https://www.twse.com.tw/",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def fetch_twse(trade_date: str, fetcher: Callable[[str], Any] | None = None) -> list[dict[str, Any]]:
    day, rows = parse_daytrade((fetcher or _default_fetcher)(TWSE_URL.format(ymd=trade_date.replace("-", ""))))
    return rows if day == trade_date else []


def fetch_tpex(trade_date: str, fetcher: Callable[[str], Any] | None = None) -> tuple[list[dict[str, Any]], str]:
    """先直接抓櫃買中心（正式站主機通常被擋），不行就看 tw-groups data 分支的鏡像；那天還沒有就回空。"""
    call = fetcher or _default_fetcher
    try:
        day, rows = parse_daytrade(call(TPEX_URL.format(slash=trade_date.replace("-", "/"))))
        if day == trade_date and rows:
            return rows, "tpex"
    except Exception as error:  # noqa: BLE001
        logger.info("櫃買中心當沖直接抓不到（%s），改用鏡像", f"{type(error).__name__}: {error}"[:120])
    try:
        day, rows = parse_daytrade(call(f"{TPEX_MIRROR_BASE}/daytrade-{trade_date}.json?v={int(time.time())}"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return [], "mirror"
        raise
    return (rows if day == trade_date else []), "mirror"


def _recent_trading_days(now: datetime, count: int) -> list[str]:
    today = now.date()
    cursor = today if (is_trading_day(today) and now.hour * 60 + now.minute >= PUBLISH_MINUTE) else previous_trading_day(today)
    days: list[date] = []
    while len(days) < count:
        days.append(cursor)
        cursor = previous_trading_day(cursor)
    return [d.isoformat() for d in days]


def collect_once(now: datetime | None = None, fetcher: Callable[[str], Any] | None = None, delay: float = 2.0) -> dict[str, Any]:
    now = now or datetime.now(TW_TZ)
    result: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "twse": [], "tpex": [], "errors": []}
    for day in _recent_trading_days(now, BACKFILL_DAYS):
        if not has_market(day, "TSE"):
            try:
                rows = fetch_twse(day, fetcher)
                result["twse"].append({"date": day, "rows": save_daytrade(day, "TSE", rows, "twse")} if rows else {"date": day, "rows": 0, "note": "尚未公布"})
            except Exception as error:  # noqa: BLE001
                result["errors"].append(f"TSE {day}: {type(error).__name__}: {error}"[:200])
            time.sleep(delay)
        if not has_market(day, "OTC"):
            try:
                rows, source = fetch_tpex(day, fetcher)
                if rows:
                    result["tpex"].append({"date": day, "rows": save_daytrade(day, "OTC", rows, source), "source": source})
            except Exception as error:  # noqa: BLE001
                result["errors"].append(f"OTC {day}: {type(error).__name__}: {error}"[:200])
    return result


def collector_status() -> dict[str, Any]:
    return {"enabled": _enabled(), "pollSeconds": POLL_SECONDS, "dates": stored_dates(5), **_state}


def run_collect() -> dict[str, Any]:
    now = datetime.now(TW_TZ)
    try:
        result = collect_once(now)
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastResult": result, "lastError": None})
    except Exception as error:  # noqa: BLE001
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastError": f"{type(error).__name__}: {error}"[:300]})
        logger.exception("現股當沖收集失敗")
        result = {"error": _state["lastError"]}
    return result


def _loop() -> None:
    while True:
        run_collect()
        time.sleep(POLL_SECONDS)


def start_daytrade_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if not _enabled():
            logger.info("現股當沖收集已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-daytrade-rank", daemon=True).start()
        _started = True
        return True


# ------------------------------------------------------------------ 排行

def _day_bars(trade_date: str, codes: list[str]) -> dict[str, dict[str, Any]]:
    """{代號: {close, prevClose, volume(張)}}：那天的官方日K＋前一根收盤。"""
    if not codes:
        return {}
    since = (date.fromisoformat(trade_date) - timedelta(days=20)).isoformat()
    out: dict[str, dict[str, Any]] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"""SELECT stock_code, substr(bar_time, 1, 10) AS d, close, volume FROM bars_1d
                    WHERE stock_code IN ({','.join('?' for _ in batch)}) AND bar_time >= ? AND bar_time < ?
                    ORDER BY stock_code, bar_time""",
                (*batch, since, f"{trade_date}z"),
            ).fetchall()
            series: dict[str, list[Any]] = {}
            for row in rows:
                series.setdefault(str(row["stock_code"]).strip().upper(), []).append(row)
            for code, bars in series.items():
                if str(bars[-1]["d"]) != trade_date:
                    continue
                out[code] = {"close": float(bars[-1]["close"]), "prevClose": float(bars[-2]["close"]) if len(bars) > 1 else None,
                             "volume": float(bars[-1]["volume"] or 0)}
    return out


def daytrade_rank(top: int = 20, trade_date: str | None = None) -> dict[str, Any]:
    """trade_date 沒給＝最新有上市資料的一天；一般個股（4 位數、不含 00 開頭 ETF），照當沖張數排。"""
    top = max(1, min(MAX_TOP, int(top)))
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        if not trade_date:
            row = connection.execute("SELECT MAX(trade_date) AS d FROM daytrade_daily WHERE market = 'TSE'").fetchone()
            trade_date = row["d"] if row else None
        if not trade_date:
            return {"status": "empty", "message": "還沒有現股當沖資料", "rows": []}
        rows = connection.execute(
            "SELECT stock_code, market, stock_name, dt_shares, buy_amount, sell_amount, suspend FROM daytrade_daily WHERE trade_date = ?",
            (trade_date,),
        ).fetchall()
    stocks = [r for r in rows if len(str(r["stock_code"])) == 4 and str(r["stock_code"]).isdigit() and not str(r["stock_code"]).startswith("00")]
    stocks.sort(key=lambda r: (-int(r["dt_shares"]), str(r["stock_code"])))
    picked = stocks[:top]
    bars = _day_bars(trade_date, [str(r["stock_code"]) for r in picked])
    out = []
    for r in picked:
        code = str(r["stock_code"])
        bar = bars.get(code) or {}
        volume = bar.get("volume") or 0
        close, prev = bar.get("close"), bar.get("prevClose")
        out.append({
            "code": code, "name": r["stock_name"] or code, "market": r["market"],
            "dtLots": int(round(int(r["dt_shares"]) / 1000)),
            "ratio": round(int(r["dt_shares"]) / (volume * 1000) * 100, 2) if volume > 0 else None,   # 當沖占比 %
            "price": close, "chg": round((close / prev - 1) * 100, 2) if close and prev else None,
            "buyAmount": r["buy_amount"], "sellAmount": r["sell_amount"], "suspend": r["suspend"] or "",
        })
    markets = sorted({str(r["market"]) for r in rows})
    return {"status": "ok", "date": trade_date, "markets": markets, "partial": "OTC" not in markets, "top": top,
            "rows": out, "dates": [d["date"] for d in stored_dates(10)]}
