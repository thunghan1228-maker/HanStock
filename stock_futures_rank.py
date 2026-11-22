"""股票期貨成交量排行（2026-11-22 使用者：做一個股票期貨成交量大的前 30 名排行榜）。

資料：
- 成交量：期交所開放資料「期貨每日交易行情」（openapi.taifex.com.tw/v1/DailyMarketReportFut），只有最新一個交易日；
  每個契約（股期代號＝兩碼＋F，例：台積電 CDF、小型台積電 QFF）全部月份加總，跨月價差那幾列不算。
  一個交易日＝前一晚的盤後時段＋當天的一般時段（期交所的算法），成交量兩個加起來；未平倉、近月漲跌用一般時段。
- 契約對到哪一檔股票：期交所「股票期貨、選擇權商品標的」頁（第 1 欄兩碼代號、第 3 欄股票代號、第 4 欄簡稱、第 12 欄每口股數：
  2,000＝一般、100＝小型，ETF 期貨另有 1,000／10,000），半天抓一次。
收盤後期交所才公布；收集器 13:50 以後每 20 分鐘看一次，抓到當天的就停到下一個交易日，每天存一份（看得到比前一天量多少）。

買進／賣出前 30（2026-11-22 使用者：除了成交量，再分買進前 30、賣出前 30）：期貨每筆成交都是一買一賣，期交所也沒有
「各檔股期的三大法人」（只有全部股期合計），所以用期交所「大額交易人未沖銷部位」（網站下載 largeTraderFutDown，可以指定日期、
抓得到前幾天）：全部月份（999999）前十大交易人的多單 − 空單＝淨部位，比前一個交易日增加＝買進、減少＝賣出；
照金額（口數 × 每口股數 × 近月收盤）排，一般、小型股期才比得起來。
"""

from __future__ import annotations

import html
import logging
import os
import re
import threading
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable

from chips_daily import _default_fetcher
from database import get_connection, initialize_database
from trading_days import is_trading_day

logger = logging.getLogger("hanstock.stock_futures_rank")
TW_TZ = timezone(timedelta(hours=8))

DAILY_URL = "https://openapi.taifex.com.tw/v1/DailyMarketReportFut"
LISTS_URL = "https://www.taifex.com.tw/cht/2/stockLists"
LISTS_REFRESH_SECONDS = 12 * 3600
LARGE_URL = "https://www.taifex.com.tw/cht/3/largeTraderFutDown?queryStartDate={start}&queryEndDate={end}"
LARGE_LOOKBACK_DAYS = 14    # 大額交易人一次抓最近兩週（連假後也找得到前一個交易日）
PUBLISH_AFTER = dt_time(13, 50)
POLL_SECONDS = 20 * 60
TOP = 30
MAX_TOP = 100

_lock = threading.Lock()
_contracts: dict[str, Any] = {"at": 0.0, "map": None}
_state: dict[str, Any] = {"lastRunAt": None, "lastResult": None, "lastError": None}
_started = False


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS stock_futures_daily (
            trade_date TEXT NOT NULL,
            contract TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            shares INTEGER,
            volume INTEGER NOT NULL,
            night_volume INTEGER NOT NULL DEFAULT 0,
            open_interest INTEGER,
            near_month TEXT,
            near_last REAL,
            near_change_pct REAL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, contract)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS stock_futures_large (
            trade_date TEXT NOT NULL,
            contract TEXT NOT NULL,
            buy5 INTEGER, sell5 INTEGER, buy10 INTEGER NOT NULL, sell10 INTEGER NOT NULL,
            inst_buy10 INTEGER, inst_sell10 INTEGER,
            oi_market INTEGER,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, contract)
        )"""
    )


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def parse_contracts(page: str) -> dict[str, dict[str, Any]]:
    """{股期代號（兩碼＋F）: {code, name, shares}}：期交所標的頁裡是股票期貨標的的那幾列。"""
    out: dict[str, dict[str, Any]] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        if len(cells) < 12 or not re.fullmatch(r"[A-Z0-9]{2}", cells[0]) or not re.fullmatch(r"\d{4,6}", cells[2]) or "是股票期貨標的" not in cells[4]:
            continue
        out[cells[0] + "F"] = {"code": cells[2], "name": cells[3], "shares": _int(cells[11])}
    return out


def _fetch_text(url: str, timeout: int = 30) -> str:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)", "Accept-Language": "zh-TW,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


def contracts(fetcher: Callable[[str], str] | None = None) -> dict[str, dict[str, Any]]:
    with _lock:
        if _contracts["map"] and time.time() - _contracts["at"] < LISTS_REFRESH_SECONDS:
            return _contracts["map"]
    mapping = parse_contracts((fetcher or _fetch_text)(LISTS_URL))
    if len(mapping) < 100:
        raise ValueError(f"期交所股票期貨標的只有 {len(mapping)} 個")
    with _lock:
        _contracts.update({"at": time.time(), "map": mapping})
    return mapping


def parse_daily(payload: Any, mapping: dict[str, dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """(交易日, 每個股期契約一列)：全部月份加總成交量（盤後＋一般）、一般時段未平倉，近月（最近的月份）收盤與漲跌幅。"""
    if not isinstance(payload, list):
        return None, []
    day = None
    agg: dict[str, dict[str, Any]] = {}
    for r in payload:
        contract, month = str(r.get("Contract") or "").strip(), str(r.get("ContractMonth(Week)") or "").strip()
        info = mapping.get(contract)
        if not info or not month or "/" in month:
            continue
        raw = str(r.get("Date") or "").strip()
        if re.fullmatch(r"\d{8}", raw):
            day = max(day or "", f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")
        a = agg.setdefault(contract, {"contract": contract, **info, "volume": 0, "night": 0, "oi": None, "near": None})
        volume = _int(r.get("Volume")) or 0
        a["volume"] += volume
        if str(r.get("TradingSession") or "").strip() == "盤後":
            a["night"] += volume
            continue
        oi = _int(r.get("OpenInterest"))
        if oi is not None:
            a["oi"] = (a["oi"] or 0) + oi
        if a["near"] is None or month < a["near"]["month"]:
            a["near"] = {"month": month, "last": _float(r.get("Last")), "pct": _float(r.get("%"))}
    rows = [a for a in agg.values() if a["volume"] > 0 or a["oi"]]
    return day, rows


def parse_large(text: str, mapping: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """期交所大額交易人下載檔（CSV）：{日期: [每個股期契約一列]}，只取全部月份合計（999999），
    交易人類別 0＝前五大／前十大交易人、1＝其中屬於特定法人的。"""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 10 or not re.fullmatch(r"\d{4}/\d{2}/\d{2}", cells[0]) or cells[3] != "999999":
            continue
        contract = cells[1] + "F"
        if contract not in mapping:
            continue
        day = cells[0].replace("/", "-")
        row = out.setdefault(day, {}).setdefault(contract, {"contract": contract})
        buy5, sell5, buy10, sell10, oi = (_int(c) for c in cells[5:10])
        if cells[4] == "0":
            row.update({"buy5": buy5, "sell5": sell5, "buy10": buy10, "sell10": sell10, "oi": oi})
        elif cells[4] == "1":
            row.update({"instBuy10": buy10, "instSell10": sell10})
    return {day: [r for r in rows.values() if r.get("buy10") is not None and r.get("sell10") is not None] for day, rows in out.items()}


def save_large(trade_date: str, rows: list[dict[str, Any]]) -> int:
    initialize_database()
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.execute("DELETE FROM stock_futures_large WHERE trade_date = ?", (trade_date,))
        connection.executemany(
            """INSERT INTO stock_futures_large (trade_date, contract, buy5, sell5, buy10, sell10, inst_buy10, inst_sell10, oi_market, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(trade_date, r["contract"], r.get("buy5"), r.get("sell5"), r["buy10"], r["sell10"], r.get("instBuy10"), r.get("instSell10"), r.get("oi"), now)
             for r in rows],
        )
    return len(rows)


def large_dates(limit: int = 5) -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        return [str(r["trade_date"]) for r in connection.execute(
            "SELECT DISTINCT trade_date FROM stock_futures_large ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()]


def _fetch_csv(url: str, timeout: int = 60) -> str:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)", "Accept-Language": "zh-TW,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("cp950", errors="replace")


def save(trade_date: str, rows: list[dict[str, Any]]) -> int:
    initialize_database()
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.execute("DELETE FROM stock_futures_daily WHERE trade_date = ?", (trade_date,))
        connection.executemany(
            """INSERT INTO stock_futures_daily (trade_date, contract, stock_code, stock_name, shares, volume, night_volume, open_interest,
                   near_month, near_last, near_change_pct, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(trade_date, r["contract"], r["code"], r["name"], r["shares"], r["volume"], r["night"], r["oi"],
              (r["near"] or {}).get("month"), (r["near"] or {}).get("last"), (r["near"] or {}).get("pct"), now) for r in rows],
        )
    return len(rows)


def stored_dates(limit: int = 5) -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        return [str(r["trade_date"]) for r in connection.execute(
            "SELECT DISTINCT trade_date FROM stock_futures_daily ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()]


def collect_once(fetcher: Callable[[str], Any] | None = None, text_fetcher: Callable[[str], str] | None = None,
                 csv_fetcher: Callable[[str], str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    mapping = contracts(text_fetcher)
    day, rows = parse_daily((fetcher or _default_fetcher)(DAILY_URL), mapping)
    result: dict[str, Any] = {"date": day, "saved": save(day, rows) if day and rows else 0}
    now = now or datetime.now(TW_TZ)
    start = (now.date() - timedelta(days=LARGE_LOOKBACK_DAYS)).strftime("%Y/%m/%d").replace("/", "%2F")
    end = now.date().strftime("%Y/%m/%d").replace("/", "%2F")
    try:
        by_day = parse_large((csv_fetcher or _fetch_csv)(LARGE_URL.format(start=start, end=end)), mapping)
        have = set(large_dates(LARGE_LOOKBACK_DAYS))
        latest = max(by_day) if by_day else None
        saved = {d: save_large(d, r) for d, r in sorted(by_day.items()) if r and (d not in have or d == latest)}
        result["large"] = {"dates": sorted(by_day), "saved": saved}
    except Exception as error:  # noqa: BLE001  行情存好了，大額交易人下一輪再試
        result["large"] = {"error": f"{type(error).__name__}: {error}"[:200]}
    return result


def _flows(top: int) -> dict[str, Any]:
    """大額交易人買進／賣出：最新兩天全部月份前十大交易人淨部位（多單 − 空單）的差，照金額排。"""
    dates = large_dates(2)
    if len(dates) < 2:
        return {"flowDate": dates[0] if dates else None, "flowPrevDate": None, "buy": [], "sell": []}
    with get_connection() as connection:
        today = connection.execute("SELECT * FROM stock_futures_large WHERE trade_date = ?", (dates[0],)).fetchall()
        prev = {str(r["contract"]): r for r in connection.execute("SELECT * FROM stock_futures_large WHERE trade_date = ?", (dates[1],)).fetchall()}
        info_day = connection.execute("SELECT MAX(trade_date) AS d FROM stock_futures_daily WHERE trade_date <= ?", (dates[0],)).fetchone()["d"]
        info = {str(r["contract"]): r for r in connection.execute(   # 代號、每口股數、近月收盤：同一天的行情（沒有就用之前最近一天）
            "SELECT contract, stock_code, stock_name, shares, near_last, near_change_pct FROM stock_futures_daily WHERE trade_date = ?", (info_day,)).fetchall()}
    rows = []
    for r in today:
        contract, p, f = str(r["contract"]), prev.get(str(r["contract"])), info.get(str(r["contract"]))
        if not p or not f:
            continue
        net, prev_net = int(r["buy10"]) - int(r["sell10"]), int(p["buy10"]) - int(p["sell10"])
        change = net - prev_net
        if change == 0:
            continue
        code, shares, price = str(f["stock_code"]), f["shares"], f["near_last"]
        amount = change * shares * price / 1e8 if shares and price else None
        inst = int(r["inst_buy10"]) - int(r["inst_sell10"]) if r["inst_buy10"] is not None and r["inst_sell10"] is not None else None
        rows.append({
            "contract": contract, "code": code, "name": f["stock_name"], "shares": shares,
            "kind": "etf" if code.startswith("00") else "mini" if shares == 100 else "std",
            "change": change, "lots": round(change * shares / 1000, 1) if shares else None, "amount": round(amount, 2) if amount is not None else None,
            "net": net, "prevNet": prev_net, "buy10": int(r["buy10"]), "sell10": int(r["sell10"]), "instNet": inst, "oi": r["oi_market"],
            "last": price, "chg": f["near_change_pct"],
        })
    key = lambda x: x["amount"] if x["amount"] is not None else 0.0   # noqa: E731  沒有價格的排在有金額的後面
    buy = sorted((x for x in rows if x["change"] > 0), key=lambda x: (-key(x), -x["change"], x["contract"]))[:top]
    sell = sorted((x for x in rows if x["change"] < 0), key=lambda x: (key(x), x["change"], x["contract"]))[:top]
    for side in (buy, sell):
        for i, x in enumerate(side):
            x["rank"] = i + 1
    return {"flowDate": dates[0], "flowPrevDate": dates[1], "buy": buy, "sell": sell}


def payload(top: int = TOP) -> dict[str, Any]:
    """最新一個交易日股期成交量前 top 名（契約為單位，一般、小型分開），附前一個交易日的量與名次。"""
    top = max(1, min(MAX_TOP, int(top)))
    dates = stored_dates(2)
    if not dates:
        return {"status": "empty", "message": "還沒有股票期貨成交資料", "date": None, "rows": []}
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM stock_futures_daily WHERE trade_date = ? ORDER BY volume DESC, contract", (dates[0],)
        ).fetchall()
        prev_rows = connection.execute(
            "SELECT contract, volume FROM stock_futures_daily WHERE trade_date = ? ORDER BY volume DESC, contract", (dates[1],)
        ).fetchall() if len(dates) > 1 else []
    prev = {str(r["contract"]): (i + 1, int(r["volume"])) for i, r in enumerate(prev_rows)}
    out = []
    for i, r in enumerate(rows[:top]):
        p = prev.get(str(r["contract"]))
        code = str(r["stock_code"])
        shares = r["shares"]
        out.append({
            "rank": i + 1, "contract": r["contract"], "code": code, "name": r["stock_name"], "shares": shares,
            "kind": "etf" if code.startswith("00") else "mini" if shares == 100 else "std",
            "volume": int(r["volume"]), "night": int(r["night_volume"]), "oi": r["open_interest"],
            "month": r["near_month"], "last": r["near_last"], "chg": r["near_change_pct"],
            "prevVolume": p[1] if p else None, "prevRank": p[0] if p else None,
        })
    total = sum(int(r["volume"]) for r in rows)
    return {"status": "ok", "date": dates[0], "prevDate": dates[1] if len(dates) > 1 else None, "top": top,
            "contracts": len(rows), "totalVolume": total, "rows": out, **_flows(top), **{k: _state.get(k) for k in ("lastRunAt",)}}


def collector_status() -> dict[str, Any]:
    return {"pollSeconds": POLL_SECONDS, "stored": stored_dates(5), "largeStored": large_dates(5), **_state}


def run_collect() -> dict[str, Any]:
    now = datetime.now(TW_TZ)
    try:
        result = collect_once()
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastResult": result, "lastError": None})
    except Exception as error:  # noqa: BLE001
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastError": f"{type(error).__name__}: {error}"[:300]})
        logger.exception("股票期貨成交量收集失敗")
        result = {"error": _state["lastError"]}
    return result


def _due(now: datetime, latest: str | None) -> bool:
    """該不該去抓：還沒有任何資料就抓；交易日 13:50 以後、還沒有今天的就抓。"""
    if latest is None:
        return True
    return is_trading_day(now.date()) and now.time().replace(tzinfo=None) >= PUBLISH_AFTER and latest < now.date().isoformat()


def _loop() -> None:
    while True:
        try:
            now, daily, large = datetime.now(TW_TZ), stored_dates(1), large_dates(1)
            if _due(now, daily[0] if daily else None) or _due(now, large[0] if large else None):
                run_collect()
        except Exception:  # noqa: BLE001
            logger.exception("股票期貨成交量收集迴圈")
        time.sleep(POLL_SECONDS)


def start_stock_futures_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_STOCK_FUTURES_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return False
        threading.Thread(target=_loop, name="hanstock-stock-futures", daemon=True).start()
        _started = True
        return True
