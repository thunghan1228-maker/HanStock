"""盤後籌碼（第一步）：每天收盤後的「主力大單」與「三大法人買賣超」，給前端「盤後籌碼排行」用。

使用者 2026-09-25：盤後籌碼排行先做；只用免費資料。
- 主力大單：我們自己用永豐逐筆算的大單淨額（main_force_bars 依交易日加總），每天都有。
- 三大法人（外資、投信、自營商）：
  - 上市：證交所「三大法人買賣超日報」（T86），收盤後當天公布，正式站主機直接抓得到，也能指定日期回補。
  - 上櫃：櫃買中心開放資料只有「最新一天」，而且擋我們的主機；改由 tw-groups 的排程工作流程（GitHub 的
    runner）每天 16:40 抓下來推到 tw-groups 的 data 分支，後端再從 GitHub 拉回來（HANSTOCK_TPEX_MIRROR_BASE），
    順便留每一天的檔案讓後端補缺的日子。後端仍會先試直接抓，哪天不擋了就不用繞。
單位：資料庫存「股」（官方原始單位），端點回「張」（÷1000）。
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
from stock_groups import industry_group_codes
from trading_days import is_trading_day, previous_trading_day

logger = logging.getLogger("hanstock.chips_daily")
TW_TZ = timezone(timedelta(hours=8))

TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?date={ymd}&selectType=ALLBUT0999&response=json"
TPEX_OPENAPI_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
TPEX_MIRROR_BASE = os.getenv("HANSTOCK_TPEX_MIRROR_BASE", "https://raw.githubusercontent.com/thunghan1228-maker/tw-groups/data/tpex")
PUBLISH_MINUTE = 15 * 60          # 證交所三大法人約 15:00 後公布；那之前不去抓當天的
BACKFILL_DAYS = int(os.getenv("HANSTOCK_CHIPS_BACKFILL_DAYS", "12"))   # 回補最近幾個交易日
POLL_SECONDS = 15 * 60
STREAK_DAYS = 30

_started = False
_lock = threading.Lock()
_state: dict[str, Any] = {"lastRunAt": None, "lastResult": None, "lastError": None}


def _enabled() -> bool:
    return os.getenv("HANSTOCK_CHIPS_COLLECTOR_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


# ------------------------------------------------------------------ 資料表

def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS institutional_daily (
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            market TEXT NOT NULL,
            stock_name TEXT,
            foreign_net INTEGER NOT NULL,
            trust_net INTEGER NOT NULL,
            dealer_net INTEGER NOT NULL,
            total_net INTEGER NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, stock_code)
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_institutional_daily_date ON institutional_daily (trade_date)")


def save_institutional(trade_date: str, market: str, rows: list[dict[str, Any]], source: str) -> int:
    """存一天一個市場的法人買賣超（股）。同一天同一檔重抓就覆蓋。"""
    if not rows:
        return 0
    initialize_database()
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO institutional_daily (trade_date, stock_code, market, stock_name, foreign_net, trust_net, dealer_net, total_net, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(trade_date, r["code"], market, r.get("name"), int(r["foreign"]), int(r["trust"]), int(r["dealer"]), int(r["total"]), source, now) for r in rows],
        )
    return len(rows)


def stored_dates(limit: int = STREAK_DAYS, market: str | None = None) -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        if market:
            rows = connection.execute("SELECT DISTINCT trade_date FROM institutional_daily WHERE market = ? ORDER BY trade_date DESC LIMIT ?", (market, limit)).fetchall()
        else:
            rows = connection.execute("SELECT DISTINCT trade_date FROM institutional_daily ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()
    return [str(row["trade_date"]) for row in rows]


def has_market(trade_date: str, market: str) -> bool:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT 1 FROM institutional_daily WHERE trade_date = ? AND market = ? LIMIT 1", (trade_date, market)).fetchone()
    return row is not None


# ------------------------------------------------------------------ 證交所 T86（上市）

def _num(value: Any) -> int:
    text = str(value if value is not None else "0").strip().replace(",", "")
    if text in {"", "-", "--"}:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_twse_t86(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """證交所三大法人買賣超日報：回 (交易日, rows)；休市或還沒公布 stat 不是 OK → (None, [])。
    外資＝外陸資（不含外資自營商）＋外資自營商；自營商＝自行買賣＋避險（官方「自營商買賣超股數」）。"""
    if not isinstance(payload, dict) or str(payload.get("stat") or "").upper() != "OK":
        return None, []
    fields = [str(f) for f in (payload.get("fields") or [])]
    idx = {name: i for i, name in enumerate(fields)}

    def col(row, name):
        i = idx.get(name)
        return _num(row[i]) if i is not None and i < len(row) else 0

    need = ("證券代號", "外陸資買賣超股數(不含外資自營商)", "投信買賣超股數", "自營商買賣超股數", "三大法人買賣超股數")
    if any(name not in idx for name in need):
        raise RuntimeError(f"T86 欄位不符: {fields}")
    raw_date = str(payload.get("date") or "")
    trade_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else None
    rows = []
    for row in payload.get("data") or []:
        if not isinstance(row, list) or len(row) < 2:
            continue
        code = str(row[0]).strip().upper()
        if not code:
            continue
        rows.append({
            "code": code, "name": str(row[1]).strip(),
            "foreign": col(row, "外陸資買賣超股數(不含外資自營商)") + col(row, "外資自營商買賣超股數"),
            "trust": col(row, "投信買賣超股數"),
            "dealer": col(row, "自營商買賣超股數"),
            "total": col(row, "三大法人買賣超股數"),
        })
    return trade_date, rows


def _default_fetcher(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={
        "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-TW,zh;q=0.9",
        "User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)",
        "Referer": "https://www.tpex.org.tw/" if "tpex" in url else "https://www.twse.com.tw/",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def fetch_twse_t86(trade_date: str, fetcher: Callable[[str], Any] | None = None) -> list[dict[str, Any]]:
    call = fetcher or _default_fetcher
    payload = call(TWSE_T86_URL.format(ymd=trade_date.replace("-", "")))
    parsed_date, rows = parse_twse_t86(payload)
    if parsed_date and parsed_date != trade_date:
        raise RuntimeError(f"T86 回的日期 {parsed_date} 不是 {trade_date}")
    return rows


# ------------------------------------------------------------------ 櫃買中心（上櫃）

def _roc_to_iso(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 7 and text.isdigit():
        return f"{int(text[:3]) + 1911:04d}-{text[3:5]}-{text[5:7]}"
    if len(text) == 10 and text[4] == "-":
        return text
    return None


def parse_tpex_3insti(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """櫃買中心開放資料（tpex_3insti_daily_trading）：欄位名有奇怪的空白，比對時把空白拿掉。
    外資＝含外資自營商的合計欄；沒有就用「不含」＋外資自營商。回 (交易日, rows)，單位股。"""
    items = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return None, []
    rows = []
    dates: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        flat = {str(k).replace(" ", ""): v for k, v in item.items()}
        code = str(flat.get("SecuritiesCompanyCode") or "").strip().upper()
        day = _roc_to_iso(flat.get("Date"))
        if not code or not day:
            continue
        foreign_all = flat.get("ForeignInvestorsIncludeMainlandAreaInvestors-Difference")
        if foreign_all is None:
            foreign = _num(flat.get("ForeignInvestorsincludeMainlandAreaInvestors(ForeignDealersexcluded)-Difference")) + _num(flat.get("ForeignDealers-Difference"))
        else:
            foreign = _num(foreign_all)
        trust = _num(flat.get("SecuritiesInvestmentTrustCompanies-Difference"))
        dealer = _num(flat.get("Dealers-Difference"))
        total_raw = flat.get("TotalDifference")
        total = _num(total_raw) if total_raw is not None else foreign + trust + dealer
        rows.append({"code": code, "name": str(flat.get("CompanyName") or "").strip(), "date": day,
                     "foreign": foreign, "trust": trust, "dealer": dealer, "total": total})
        dates[day] = dates.get(day, 0) + 1
    if not rows:
        return None, []
    latest = max(dates)
    return latest, [r for r in rows if r["date"] == latest]


def _mirror_url(name: str, *, volatile: bool) -> str:
    """鏡像檔案網址。index.json 跟 3insti-latest.json 會一直變，GitHub 的內容快取約 5 分鐘，排程主機推完馬上戳
    後端時會拿到舊的，所以加時間參數避開快取；每天一份的檔案寫了就不會改，照常快取。"""
    url = f"{TPEX_MIRROR_BASE}/{name}"
    return f"{url}?v={int(time.time())}" if volatile else url


def fetch_tpex_latest(fetcher: Callable[[str], Any] | None = None) -> tuple[str | None, list[dict[str, Any]], str]:
    """先直接抓櫃買中心，抓不到（正式站主機被擋）就用 tw-groups data 分支的鏡像。回 (日期, rows, 來源)。"""
    call = fetcher or _default_fetcher
    try:
        day, rows = parse_tpex_3insti(call(TPEX_OPENAPI_URL))
        if day and len(rows) >= 600:
            return day, rows, "tpex"
    except Exception as error:  # noqa: BLE001
        logger.info("櫃買中心直接抓不到（%s），改用鏡像", f"{type(error).__name__}: {error}"[:120])
    day, rows = parse_tpex_3insti(call(_mirror_url("3insti-latest.json", volatile=True)))
    return day, rows, "mirror"


def fetch_tpex_mirror_index(fetcher: Callable[[str], Any] | None = None) -> list[str]:
    """鏡像裡有哪些日子（tpex/index.json）；抓不到就當空的，缺的日子這一輪就不問。"""
    call = fetcher or _default_fetcher
    try:
        payload = call(_mirror_url("index.json", volatile=True))
    except Exception:  # noqa: BLE001
        return []
    return [str(d) for d in payload if isinstance(d, str)] if isinstance(payload, list) else []


def fetch_tpex_mirror_date(trade_date: str, fetcher: Callable[[str], Any] | None = None) -> list[dict[str, Any]]:
    """鏡像裡某一天的檔案（沒有那天就回空）。"""
    call = fetcher or _default_fetcher
    try:
        day, rows = parse_tpex_3insti(call(_mirror_url(f"3insti-{trade_date}.json", volatile=False)))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []
        raise
    return rows if day == trade_date else []


# ------------------------------------------------------------------ 主力大單（自己的資料）與收盤價

def main_force_daily(trade_date: str, codes: list[str]) -> dict[str, dict[str, Any]]:
    """{代號: {net(張), buyAmount, sellAmount, totalAmount, netAmount, pct}}：main_force_bars 5 分K依交易日加總。"""
    if not codes:
        return {}
    initialize_database()
    out: dict[str, dict[str, Any]] = {}
    with get_connection() as connection:
        exists = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'main_force_bars'").fetchone()
        if not exists:
            return {}
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"""SELECT stock_code, SUM(main_net_volume) AS net, SUM(main_buy_amount) AS buy_amount, SUM(main_sell_amount) AS sell_amount,
                           MAX(total_amount) AS total_amount
                    FROM main_force_bars WHERE trade_date = ? AND interval = '5m' AND stock_code IN ({','.join('?' for _ in batch)})
                    GROUP BY stock_code""",
                (trade_date, *batch),
            ).fetchall()
            for row in rows:
                buy = float(row["buy_amount"] or 0)
                sell = float(row["sell_amount"] or 0)
                total = float(row["total_amount"] or 0)
                out[str(row["stock_code"]).upper()] = {
                    "net": int(row["net"] or 0), "buyAmount": round(buy), "sellAmount": round(sell), "netAmount": round(buy - sell),
                    "totalAmount": round(total), "pct": round((buy - sell) / total * 100, 1) if total > 0 else None,
                }
    return out


def main_force_dates(limit: int = STREAK_DAYS) -> list[str]:
    initialize_database()
    with get_connection() as connection:
        exists = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'main_force_bars'").fetchone()
        if not exists:
            return []
        rows = connection.execute("SELECT DISTINCT trade_date FROM main_force_bars WHERE interval = '5m' ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()
    return [str(row["trade_date"]) for row in rows]


def daily_quotes(trade_date: str, codes: list[str]) -> dict[str, dict[str, Any]]:
    """{代號: {close, prevClose, changePct, volume(張)}}：那天的日K與前一根。"""
    if not codes:
        return {}
    initialize_database()
    since = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
    bars: dict[str, list[tuple[str, float, int]]] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"""SELECT stock_code, substr(bar_time, 1, 10) AS d, close, volume FROM bars_1d
                    WHERE stock_code IN ({','.join('?' for _ in batch)}) AND substr(bar_time, 1, 10) <= ? AND substr(bar_time, 1, 10) >= ?
                    ORDER BY stock_code, d""",
                (*batch, trade_date, since),
            ).fetchall()
            for row in rows:
                bars.setdefault(str(row["stock_code"]).upper(), []).append((str(row["d"]), float(row["close"]), int(row["volume"] or 0)))
    out: dict[str, dict[str, Any]] = {}
    for code, series in bars.items():
        if not series or series[-1][0] != trade_date:
            continue
        close, volume = series[-1][1], series[-1][2]
        prev = series[-2][1] if len(series) >= 2 else None
        out[code] = {"close": close, "prevClose": prev, "volume": volume,
                     "changePct": round((close / prev - 1) * 100, 2) if prev else None}
    return out


# ------------------------------------------------------------------ 組合給前端

MEASURES = ("foreign", "trust", "dealer", "total")


def _institutional_by_date(dates: list[str], codes: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """{date: {code: {foreign, trust, dealer, total(股), market, name}}}"""
    if not dates or not codes:
        return {}
    initialize_database()
    out: dict[str, dict[str, dict[str, Any]]] = {d: {} for d in dates}
    with get_connection() as connection:
        _schema(connection)
        for start in range(0, len(codes), 300):
            batch = codes[start:start + 300]
            rows = connection.execute(
                f"""SELECT trade_date, stock_code, market, stock_name, foreign_net, trust_net, dealer_net, total_net FROM institutional_daily
                    WHERE trade_date IN ({','.join('?' for _ in dates)}) AND stock_code IN ({','.join('?' for _ in batch)})""",
                (*dates, *batch),
            ).fetchall()
            for row in rows:
                out[str(row["trade_date"])][str(row["stock_code"]).upper()] = {
                    "foreign": int(row["foreign_net"]), "trust": int(row["trust_net"]), "dealer": int(row["dealer_net"]), "total": int(row["total_net"]),
                    "market": str(row["market"]), "name": row["stock_name"],
                }
    return out


def _streak(values: list[int | None]) -> int:
    """values 新的在前：連續同方向的天數，買超為正、賣超為負；第一天是 0 或沒資料就是 0。"""
    if not values or not values[0]:
        return 0
    sign = 1 if values[0] > 0 else -1
    n = 0
    for v in values:
        if v is None or v == 0 or (v > 0) != (sign > 0):
            break
        n += 1
    return n * sign


def chips_daily(trade_date: str | None = None) -> dict[str, Any]:
    """一天的盤後籌碼：只回 43 個族群的股票。date 沒給就用最新有法人資料的那天（都沒有就用最新有主力資料的那天）。"""
    from brew_launch_history import group_and_name

    inst_dates = stored_dates(STREAK_DAYS)
    mf_dates = main_force_dates(STREAK_DAYS)
    all_dates = sorted(set(inst_dates) | set(mf_dates), reverse=True)
    if not trade_date:
        trade_date = inst_dates[0] if inst_dates else (mf_dates[0] if mf_dates else None)
    if not trade_date:
        return {"status": "empty", "date": None, "dates": [], "stocks": {}, "sources": {}}
    codes = sorted(industry_group_codes())
    # 連續天數：從這一天往前看最近 STREAK_DAYS 個有資料的交易日
    inst_window = [d for d in inst_dates if d <= trade_date][:STREAK_DAYS]
    mf_window = [d for d in mf_dates if d <= trade_date][:STREAK_DAYS]
    inst = _institutional_by_date(inst_window, codes)
    mf_by_date = {d: main_force_daily(d, codes) for d in mf_window}
    quotes = daily_quotes(trade_date, codes)
    today_inst = inst.get(trade_date, {})
    today_mf = mf_by_date.get(trade_date, {})
    stocks: dict[str, Any] = {}
    for code in codes:
        row_inst = today_inst.get(code)
        row_mf = today_mf.get(code)
        if not row_inst and not row_mf:
            continue
        group, name = group_and_name(code)
        streak = {m: _streak([(inst.get(d, {}).get(code) or {}).get(m) for d in inst_window]) for m in MEASURES}
        streak["mf"] = _streak([(mf_by_date.get(d, {}).get(code) or {}).get("net") for d in mf_window])
        entry: dict[str, Any] = {
            "name": (row_inst or {}).get("name") or name, "group": group,
            "market": (row_inst or {}).get("market"),
            "streak": streak,
            **(quotes.get(code) or {"close": None, "prevClose": None, "changePct": None, "volume": None}),
        }
        for m in MEASURES:
            entry[m] = round(row_inst[m] / 1000, 1) if row_inst else None   # 張
        entry["mf"] = row_mf
        stocks[code] = entry
    with get_connection() as connection:
        _schema(connection)
        src_rows = connection.execute(
            "SELECT market, source, COUNT(*) AS n, MAX(updated_at) AS at FROM institutional_daily WHERE trade_date = ? GROUP BY market, source", (trade_date,)
        ).fetchall()
    sources = {str(r["market"]): {"source": str(r["source"]), "rows": int(r["n"]), "updatedAt": r["at"]} for r in src_rows}
    return {
        "status": "ok", "date": trade_date, "dates": all_dates, "institutionalDates": inst_dates, "mainForceDates": mf_dates,
        "sources": sources, "mainForceRows": len(today_mf), "stockCount": len(stocks), "stocks": stocks,
        "generatedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------ 收集

def _recent_trading_days(now: datetime, count: int) -> list[str]:
    """now 之前（含當天，若已過公布時間）最近 count 個交易日，新的在前。"""
    today = now.date()
    days: list[date] = []
    cursor = today if (is_trading_day(today) and now.hour * 60 + now.minute >= PUBLISH_MINUTE) else previous_trading_day(today)
    while len(days) < count:
        days.append(cursor)
        cursor = previous_trading_day(cursor)
    return [d.isoformat() for d in days]


def collect_once(now: datetime | None = None, fetcher: Callable[[str], Any] | None = None, delay: float = 2.0) -> dict[str, Any]:
    """抓還沒存的日子：上市直接抓證交所（可指定日期）；上櫃抓最新一天（直接／鏡像），缺的日子再問鏡像有沒有那天的檔。"""
    now = now or datetime.now(TW_TZ)
    result: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "twse": [], "tpex": [], "errors": []}
    wanted = _recent_trading_days(now, BACKFILL_DAYS)
    # 上市
    for day in wanted:
        if has_market(day, "TSE"):
            continue
        try:
            rows = fetch_twse_t86(day, fetcher)
        except Exception as error:  # noqa: BLE001
            result["errors"].append(f"TSE {day}: {type(error).__name__}: {error}"[:200])
            break  # 連不上就先停，下一輪再試，不要連續打
        if rows:
            save_institutional(day, "TSE", rows, "twse")
            result["twse"].append({"date": day, "rows": len(rows)})
        else:
            result["twse"].append({"date": day, "rows": 0, "note": "尚未公布或休市"})
        time.sleep(delay)
    # 上櫃：最新一天
    try:
        day, rows, source = fetch_tpex_latest(fetcher)
        if day and rows and not has_market(day, "OTC"):
            save_institutional(day, "OTC", rows, source)
            result["tpex"].append({"date": day, "rows": len(rows), "source": source})
    except Exception as error:  # noqa: BLE001
        result["errors"].append(f"OTC latest: {type(error).__name__}: {error}"[:200])
    # 上櫃：缺的日子問鏡像（先看鏡像的日期清單，沒有的日子不用一個個試）
    missing = [day for day in wanted if not has_market(day, "OTC")]
    available = set(fetch_tpex_mirror_index(fetcher)) if missing else set()
    for day in missing:
        if day not in available:
            continue
        try:
            rows = fetch_tpex_mirror_date(day, fetcher)
        except Exception as error:  # noqa: BLE001
            result["errors"].append(f"OTC mirror {day}: {type(error).__name__}: {error}"[:200])
            break
        if rows:
            save_institutional(day, "OTC", rows, "mirror")
            result["tpex"].append({"date": day, "rows": len(rows), "source": "mirror"})
    return result


def collector_status() -> dict[str, Any]:
    return {"enabled": _enabled(), "pollSeconds": POLL_SECONDS, "backfillDays": BACKFILL_DAYS, "mirrorBase": TPEX_MIRROR_BASE, **_state}


def run_collect() -> dict[str, Any]:
    now = datetime.now(TW_TZ)
    try:
        result = collect_once(now)
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastResult": result, "lastError": None})
    except Exception as error:  # noqa: BLE001
        _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastError": f"{type(error).__name__}: {error}"[:300]})
        logger.exception("盤後籌碼收集失敗")
        result = {"error": _state["lastError"]}
    return result


def _loop() -> None:
    while True:
        run_collect()
        time.sleep(POLL_SECONDS)


def start_chips_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if not _enabled():
            logger.info("盤後籌碼收集已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-chips-daily", daemon=True).start()
        _started = True
        return True
