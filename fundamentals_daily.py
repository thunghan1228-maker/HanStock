"""基本面與集保週籌碼（波段日報第二階段，2026-09-26 使用者：照波段精選日報補齊）。

- 本益比：上市用證交所 BWIBBU_d（每日，正式站直抓）；上櫃用櫃買中心開放資料 tpex_mainboard_peratio_analysis，
  櫃買中心擋正式站主機，所以走 tw-groups 的鏡像（排程主機每個交易日 16:40 推）
- 月營收年增：上市用證交所開放資料 t187ap05_L 直抓；上櫃 mopsfin_t187ap05_O 走鏡像
- 已發行股數：上市 t187ap03_L 直抓；上櫃 mopsfin_t187ap03_O 走鏡像（法人 5 日佔股本 % 用）
- 集保股權分散（每週）：集保開放資料 1-5 整份很大，排程主機先過濾成族群股票再放鏡像 tdcc-YYYY-MM-DD.json；
  這裡算 400 張以上大戶的持股比例、週變化與連續幾週增加
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from brew_launch import group_codes
from chips_daily import _default_fetcher, _mirror_url
from database import get_connection, initialize_database
from trading_days import is_trading_day, previous_trading_day

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))

TWSE_PE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={ymd}&selectType=ALL&response=json"
TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TWSE_BASICS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
MIRROR_PE = "peratio-latest.json"
MIRROR_REVENUE = "revenue-latest.json"
MIRROR_BASICS = "basics-latest.json"
MIRROR_TDCC_INDEX = "tdcc-index.json"
PE_PUBLISH_MINUTE = 16 * 60 + 30     # 證交所本益比約 16:30 後才有當天的
BIG_HOLDER_LEVELS = (12, 13, 14, 15)  # 400 張以上（400-600、600-800、800-1000、1000 張以上）
TDCC_KEEP_WEEKS = 12
POLL_SECONDS = 30 * 60
WINDOW_START = 16 * 60 + 45
WINDOW_END = 19 * 60


# ------------------------------------------------------------------ 儲存

def _schema(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS stock_pe_daily (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, market TEXT NOT NULL,
            pe REAL, pbr REAL, yield REAL, source TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS stock_revenue_monthly (
            stock_code TEXT NOT NULL, ym TEXT NOT NULL, revenue INTEGER, yoy_pct REAL, mom_pct REAL,
            market TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (stock_code, ym)
        );
        CREATE TABLE IF NOT EXISTS stock_shares (
            stock_code TEXT PRIMARY KEY, shares INTEGER NOT NULL, market TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tdcc_weekly (
            data_date TEXT NOT NULL, stock_code TEXT NOT NULL, level INTEGER NOT NULL,
            holders INTEGER NOT NULL, shares INTEGER NOT NULL, pct REAL NOT NULL,
            PRIMARY KEY (data_date, stock_code, level)
        );
        """
    )


def _now_iso() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def save_pe(trade_date: str, market: str, rows: list[dict[str, Any]], source: str) -> int:
    if not rows:
        return 0
    initialize_database()
    now = _now_iso()
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO stock_pe_daily (trade_date, stock_code, market, pe, pbr, yield, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(trade_date, r["code"], market, r.get("pe"), r.get("pbr"), r.get("yield"), source, now) for r in rows],
        )
    return len(rows)


def save_revenue(market: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    initialize_database()
    now = _now_iso()
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO stock_revenue_monthly (stock_code, ym, revenue, yoy_pct, mom_pct, market, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r["code"], r["ym"], r.get("revenue"), r.get("yoy"), r.get("mom"), market, now) for r in rows],
        )
    return len(rows)


def save_shares(market: str, shares: dict[str, int]) -> int:
    if not shares:
        return 0
    initialize_database()
    now = _now_iso()
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO stock_shares (stock_code, shares, market, updated_at) VALUES (?, ?, ?, ?)",
            [(code, int(n), market, now) for code, n in shares.items() if n],
        )
    return len(shares)


def save_tdcc(data_date: str, rows: list[tuple[str, int, int, int, float]]) -> int:
    """rows: (代號, 分級, 人數, 股數, 比例%)。"""
    if not rows:
        return 0
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO tdcc_weekly (data_date, stock_code, level, holders, shares, pct) VALUES (?, ?, ?, ?, ?, ?)",
            [(data_date, code, int(level), int(holders), int(shares), float(pct)) for code, level, holders, shares, pct in rows],
        )
        connection.execute(
            "DELETE FROM tdcc_weekly WHERE data_date NOT IN (SELECT DISTINCT data_date FROM tdcc_weekly ORDER BY data_date DESC LIMIT ?)",
            (TDCC_KEEP_WEEKS,),
        )
    return len(rows)


def pe_dates(market: str, limit: int = 5) -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT DISTINCT trade_date FROM stock_pe_daily WHERE market = ? ORDER BY trade_date DESC LIMIT ?", (market, limit)).fetchall()
    return [str(r["trade_date"]) for r in rows]


def tdcc_dates(limit: int = TDCC_KEEP_WEEKS) -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT DISTINCT data_date FROM tdcc_weekly ORDER BY data_date DESC LIMIT ?", (limit,)).fetchall()
    return [str(r["data_date"]) for r in rows]


# ------------------------------------------------------------------ 解析

def _float(value: Any) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        if text in ("", "-", "N/A", "NA", "--"):
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    v = _float(value)
    return None if v is None else int(v)


def _roc_to_iso(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 7 and text.isdigit():
        return f"{int(text[:3]) + 1911}-{text[3:5]}-{text[5:7]}"
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return None


def _roc_ym(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 5 and text.isdigit():
        return f"{int(text[:3]) + 1911}-{text[3:5]}"
    return None


def parse_twse_pe(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """證交所 BWIBBU_d：{"stat":"OK","date":"20260924","fields":[...],"data":[[代號,名稱,收盤價,殖利率,股利年度,本益比,淨值比,財報年季]]}"""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return None, []
    fields = [str(f) for f in payload.get("fields") or []]
    idx = {name: i for i, name in enumerate(fields)}
    need = ("證券代號", "本益比")
    if any(k not in idx for k in need):
        return None, []
    day = _roc_to_iso(payload.get("date"))
    rows = []
    for row in payload.get("data") or []:
        if not isinstance(row, list) or len(row) <= idx["本益比"]:
            continue
        code = str(row[idx["證券代號"]]).strip().upper()
        if not code:
            continue
        rows.append({"code": code, "name": str(row[idx["證券名稱"]]).strip() if "證券名稱" in idx else None,
                     "pe": _float(row[idx["本益比"]]), "pbr": _float(row[idx["股價淨值比"]]) if "股價淨值比" in idx else None,
                     "yield": _float(row[idx["殖利率(%)"]]) if "殖利率(%)" in idx else None})
    return day, rows


def parse_tpex_pe(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """櫃買中心 tpex_mainboard_peratio_analysis：[{Date, SecuritiesCompanyCode, PriceEarningRatio, YieldRatio, PriceBookRatio}]"""
    if not isinstance(payload, list):
        return None, []
    day = None
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("SecuritiesCompanyCode") or "").strip().upper()
        if not code:
            continue
        day = day or _roc_to_iso(item.get("Date"))
        rows.append({"code": code, "name": str(item.get("CompanyName") or "").strip(), "pe": _float(item.get("PriceEarningRatio")),
                     "pbr": _float(item.get("PriceBookRatio")), "yield": _float(item.get("YieldRatio"))})
    return day, rows


def parse_revenue(payload: Any) -> list[dict[str, Any]]:
    """證交所／櫃買中心 t187ap05：[{資料年月:"11508", 公司代號, 營業收入-當月營收, 營業收入-去年同月增減(%), 營業收入-上月比較增減(%)}]"""
    if not isinstance(payload, list):
        return []
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("公司代號") or item.get("SecuritiesCompanyCode") or "").strip().upper()
        ym = _roc_ym(item.get("資料年月") or item.get("YearMonth"))
        if not code or not ym:
            continue
        rows.append({"code": code, "ym": ym, "revenue": _int(item.get("營業收入-當月營收")),
                     "yoy": _float(item.get("營業收入-去年同月增減(%)")), "mom": _float(item.get("營業收入-上月比較增減(%)"))})
    return rows


def parse_basics(payload: Any) -> dict[str, int]:
    """t187ap03：已發行普通股數（股）。"""
    if not isinstance(payload, list):
        return {}
    out: dict[str, int] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("公司代號") or item.get("SecuritiesCompanyCode") or "").strip().upper()
        shares = _int(item.get("已發行普通股數或TDR原股發行股數") or item.get("已發行普通股數") or item.get("IssuedShares"))
        if code and shares:
            out[code] = shares
    return out


def parse_tdcc_mirror(payload: Any) -> tuple[str | None, list[tuple[str, int, int, int, float]]]:
    """鏡像的集保檔：{"date":"2026-09-24","rows":[[代號,分級,人數,股數,比例%],...]}"""
    if not isinstance(payload, dict):
        return None, []
    day = str(payload.get("date") or "")[:10] or None
    rows = []
    for row in payload.get("rows") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        code = str(row[0]).strip().upper()
        level, holders, shares, pct = _int(row[1]), _int(row[2]), _int(row[3]), _float(row[4])
        if code and level is not None and holders is not None and shares is not None and pct is not None:
            rows.append((code, level, holders, shares, pct))
    return day, rows


# ------------------------------------------------------------------ 查詢

def latest_pe(codes: list[str]) -> dict[str, dict[str, Any]]:
    """每檔最近一天的本益比（上市、上櫃各自最新那天）。"""
    if not codes:
        return {}
    initialize_database()
    out: dict[str, dict[str, Any]] = {}
    with get_connection() as connection:
        _schema(connection)
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"""SELECT p.stock_code, p.trade_date, p.pe, p.pbr, p.yield FROM stock_pe_daily p
                    JOIN (SELECT stock_code, MAX(trade_date) AS d FROM stock_pe_daily WHERE stock_code IN ({','.join('?' for _ in batch)}) GROUP BY stock_code) m
                      ON m.stock_code = p.stock_code AND m.d = p.trade_date""",
                batch,
            ).fetchall()
            for r in rows:
                out[str(r["stock_code"]).upper()] = {"date": str(r["trade_date"]), "pe": r["pe"], "pbr": r["pbr"], "yield": r["yield"]}
    return out


def latest_revenue(codes: list[str]) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    initialize_database()
    out: dict[str, dict[str, Any]] = {}
    with get_connection() as connection:
        _schema(connection)
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"""SELECT r.stock_code, r.ym, r.revenue, r.yoy_pct, r.mom_pct FROM stock_revenue_monthly r
                    JOIN (SELECT stock_code, MAX(ym) AS ym FROM stock_revenue_monthly WHERE stock_code IN ({','.join('?' for _ in batch)}) GROUP BY stock_code) m
                      ON m.stock_code = r.stock_code AND m.ym = r.ym""",
                batch,
            ).fetchall()
            for r in rows:
                out[str(r["stock_code"]).upper()] = {"ym": str(r["ym"]), "revenue": r["revenue"], "yoy": r["yoy_pct"], "mom": r["mom_pct"]}
    return out


def shares_map(codes: list[str]) -> dict[str, int]:
    if not codes:
        return {}
    initialize_database()
    out: dict[str, int] = {}
    with get_connection() as connection:
        _schema(connection)
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(f"SELECT stock_code, shares FROM stock_shares WHERE stock_code IN ({','.join('?' for _ in batch)})", batch).fetchall()
            for r in rows:
                out[str(r["stock_code"]).upper()] = int(r["shares"])
    return out


def tdcc_summary(codes: list[str], weeks: int = 6) -> dict[str, dict[str, Any]]:
    """{代號: {date, bigPct(400 張以上持股比例), bigShares, bigChangePct(大戶張數週變化 %), bigChangePp(比例變化 百分點),
    weeks(連續幾週大戶增加), thousandPct(千張以上比例), prevDate}}"""
    dates = tdcc_dates(weeks)
    if not codes or not dates:
        return {}
    initialize_database()
    by_code: dict[str, dict[str, dict[str, float]]] = {}
    with get_connection() as connection:
        _schema(connection)
        for start in range(0, len(codes), 300):
            batch = codes[start:start + 300]
            rows = connection.execute(
                f"""SELECT data_date, stock_code, level, shares, pct FROM tdcc_weekly
                    WHERE data_date IN ({','.join('?' for _ in dates)}) AND stock_code IN ({','.join('?' for _ in batch)})
                      AND level IN ({','.join(str(x) for x in BIG_HOLDER_LEVELS)})""",
                (*dates, *batch),
            ).fetchall()
            for r in rows:
                entry = by_code.setdefault(str(r["stock_code"]).upper(), {}).setdefault(str(r["data_date"]), {"shares": 0.0, "pct": 0.0, "thousand": 0.0})
                entry["shares"] += float(r["shares"])
                entry["pct"] += float(r["pct"])
                if int(r["level"]) == 15:
                    entry["thousand"] += float(r["pct"])
    out: dict[str, dict[str, Any]] = {}
    for code, per_date in by_code.items():
        have = [d for d in dates if d in per_date]   # 新的在前
        if not have:
            continue
        latest = per_date[have[0]]
        prev = per_date[have[1]] if len(have) > 1 else None
        change_pct = round((latest["shares"] / prev["shares"] - 1) * 100, 2) if prev and prev["shares"] > 0 else None
        change_pp = round(latest["pct"] - prev["pct"], 2) if prev else None
        streak = 0
        for i in range(len(have) - 1):
            if per_date[have[i]]["shares"] > per_date[have[i + 1]]["shares"]:
                streak += 1
            else:
                break
        out[code] = {"date": have[0], "prevDate": have[1] if len(have) > 1 else None, "bigPct": round(latest["pct"], 2),
                     "bigShares": int(latest["shares"]), "bigChangePct": change_pct, "bigChangePp": change_pp,
                     "weeks": streak, "thousandPct": round(latest["thousand"], 2)}
    return out


# ------------------------------------------------------------------ 收集

_state: dict[str, Any] = {"running": False, "lastRunAt": None, "lastError": None, "sources": {}}
_lock = threading.Lock()


def collector_status() -> dict[str, Any]:
    with _lock:
        return {"running": _state["running"], "lastRunAt": _state["lastRunAt"], "lastError": _state["lastError"], "sources": dict(_state["sources"]),
                "peDates": {"TSE": pe_dates("TSE", 2), "OTC": pe_dates("OTC", 2)}, "tdccDates": tdcc_dates(4)}


def _pe_candidate_dates(now: datetime, count: int = 3) -> list[str]:
    """最近幾個交易日（今天要 16:30 後才算）。"""
    out: list[str] = []
    day = now.date()
    if not (is_trading_day(day) and now.hour * 60 + now.minute >= PE_PUBLISH_MINUTE):
        day = previous_trading_day(day)
    while len(out) < count:
        out.append(day.isoformat())
        day = previous_trading_day(day)
    return out


def collect_once(now: datetime | None = None, fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    now = now or datetime.now(TW_TZ)
    fetch = fetcher or _default_fetcher
    codes = set(group_codes())
    result: dict[str, Any] = {}

    def record(key: str, **info: Any) -> None:
        result[key] = info
        with _lock:
            _state["sources"][key] = {**info, "at": now.isoformat(timespec="seconds")}

    # 上市本益比：最近一個交易日；已經有就不重抓
    try:
        have = set(pe_dates("TSE", 5))
        saved = None
        for day in _pe_candidate_dates(now):
            if day in have:
                saved = ("cached", day, 0)
                break
            date_iso, rows = parse_twse_pe(fetch(TWSE_PE_URL.format(ymd=day.replace("-", ""))))
            rows = [r for r in rows if r["code"] in codes]
            if date_iso and rows:
                saved = ("twse", date_iso, save_pe(date_iso, "TSE", rows, "twse"))
                break
        record("peTSE", ok=saved is not None, source=saved[0] if saved else None, date=saved[1] if saved else None, rows=saved[2] if saved else 0)
    except Exception as exc:  # noqa: BLE001
        record("peTSE", ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
    # 上櫃本益比：鏡像最新
    try:
        date_iso, rows = parse_tpex_pe(fetch(_mirror_url(MIRROR_PE, volatile=True)))
        rows = [r for r in rows if r["code"] in codes]
        if date_iso and rows and date_iso not in set(pe_dates("OTC", 5)):
            record("peOTC", ok=True, source="mirror", date=date_iso, rows=save_pe(date_iso, "OTC", rows, "mirror"))
        else:
            record("peOTC", ok=bool(date_iso), source="mirror", date=date_iso, rows=0, note="沒有新的一天" if date_iso else "鏡像沒有資料")
    except Exception as exc:  # noqa: BLE001
        record("peOTC", ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
    # 月營收（上市直抓、上櫃鏡像）
    for key, market, url in (("revenueTSE", "TSE", TWSE_REVENUE_URL), ("revenueOTC", "OTC", _mirror_url(MIRROR_REVENUE, volatile=True))):
        try:
            rows = [r for r in parse_revenue(fetch(url)) if r["code"] in codes]
            record(key, ok=bool(rows), rows=save_revenue(market, rows), ym=max((r["ym"] for r in rows), default=None))
        except Exception as exc:  # noqa: BLE001
            record(key, ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
    # 已發行股數（上市直抓、上櫃鏡像）
    for key, market, url in (("sharesTSE", "TSE", TWSE_BASICS_URL), ("sharesOTC", "OTC", _mirror_url(MIRROR_BASICS, volatile=True))):
        try:
            shares = {c: n for c, n in parse_basics(fetch(url)).items() if c in codes}
            record(key, ok=bool(shares), rows=save_shares(market, shares))
        except Exception as exc:  # noqa: BLE001
            record(key, ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
    # 集保週資料：鏡像清單裡還沒存的
    try:
        index = fetch(_mirror_url(MIRROR_TDCC_INDEX, volatile=True))
        wanted = [str(d) for d in index if isinstance(d, str)] if isinstance(index, list) else []
        have = set(tdcc_dates(TDCC_KEEP_WEEKS))
        added = []
        for day in sorted(wanted, reverse=True)[:TDCC_KEEP_WEEKS]:
            if day in have:
                continue
            try:
                date_iso, rows = parse_tdcc_mirror(fetch(_mirror_url(f"tdcc-{day}.json", volatile=False)))
            except urllib.error.HTTPError:
                continue
            rows = [r for r in rows if r[0] in codes]
            if date_iso and rows:
                save_tdcc(date_iso, rows)
                added.append(date_iso)
        record("tdcc", ok=True, added=added, latest=(tdcc_dates(1) or [None])[0])
    except Exception as exc:  # noqa: BLE001
        record("tdcc", ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
    with _lock:
        _state["lastRunAt"] = now.isoformat(timespec="seconds")
        _state["lastError"] = "; ".join(f"{k}: {v.get('error')}" for k, v in result.items() if v.get("error")) or None
    return result


def _has_new_data(result: dict[str, Any], before: dict[str, Any]) -> bool:
    """收到新東西才值得重算波段日報：新的一天本益比、新的一週集保、月營收換月、已發行股數的檔數變了。"""
    for key in ("peTSE", "peOTC"):
        r = result.get(key) or {}
        if r.get("ok") and r.get("rows"):
            return True
    if (result.get("tdcc") or {}).get("added"):
        return True
    for key in ("revenueTSE", "revenueOTC"):
        r, b = result.get(key) or {}, before.get(key) or {}
        if r.get("ok") and r.get("ym") != b.get("ym"):
            return True
    for key in ("sharesTSE", "sharesOTC"):
        r, b = result.get(key) or {}, before.get(key) or {}
        if r.get("ok") and r.get("rows") != b.get("rows"):
            return True
    return False


def run_collect(now: datetime | None = None, *, only_if_new: bool = False) -> dict[str, Any]:
    """抓一輪，然後重算波段日報（only_if_new＝有抓到新資料才重算；排程用，手動戳的一定重算）。"""
    with _lock:
        before = {k: dict(v) for k, v in _state["sources"].items()}
    result = collect_once(now)
    if only_if_new and not _has_new_data(result, before):
        result["swingRefreshed"] = False
        return result
    try:
        from swing_report import run_once as run_swing_report

        run_swing_report()
        result["swingRefreshed"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("swing report refresh after fundamentals failed: %s", exc)
        result["swingRefreshed"] = False
    return result


def _loop() -> None:
    time.sleep(120)
    try:
        run_collect(only_if_new=True)
    except Exception:  # noqa: BLE001
        logger.exception("fundamentals collect failed")
    while True:
        time.sleep(POLL_SECONDS)
        now = datetime.now(TW_TZ)
        minute = now.hour * 60 + now.minute
        if is_trading_day(now) and WINDOW_START <= minute <= WINDOW_END:
            try:
                run_collect(now, only_if_new=True)
            except Exception:  # noqa: BLE001
                logger.exception("fundamentals collect failed")


def start_fundamentals_collector() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_loop, name="fundamentals-daily", daemon=True).start()
    return True
