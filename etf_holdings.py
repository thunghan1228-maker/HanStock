"""主動式 ETF 五檔每日持股（下午報第三階段，2026-09-26 使用者：五檔規模最大的台股主動式 ETF）。

各投信網站擋不擋正式站主機不一定，所以跟上櫃資料一樣走排程主機：tw-groups 的 etf-holdings 工作流程
每個交易日晚上抓統一（00981A、00403A）、復華（00991A）、群益（00982A、00992A）公告的持股清單，
整理成同一種格式推到 data 分支（tpex/etf-YYYY-MM-DD.json ＋ etf-index.json），再戳後端拉回來。
後端把每天的持股存起來，跟前一份比出新增／加碼／減碼／刪除，再看五檔有沒有同步加碼或減碼同一檔股票，
放進下午報的「主動式基金」分頁。
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from brew_launch_history import group_and_name
from chips_daily import _default_fetcher, _mirror_url
from database import get_connection, initialize_database
from trading_days import is_trading_day

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))

ETFS = [
    ("00981A", "主動統一台股增長", "統一投信"),
    ("00403A", "主動統一升級50", "統一投信"),
    ("00991A", "主動復華未來50", "復華投信"),
    ("00982A", "主動群益台灣強棒", "群益投信"),
    ("00992A", "主動群益科技創新", "群益投信"),
]
ETF_META = {code: (name, issuer) for code, name, issuer in ETFS}
MIRROR_INDEX = "etf-index.json"
FETCH_LIMIT = 15            # 一次最多補幾天
PREV_MAX_GAP_DAYS = 8       # 前一份快照最多隔幾個日曆日，超過就不算「前一天」
SYNC_MIN_ETFS = 2           # 同步加碼／減碼：至少幾檔一起
TOP_HOLDINGS = 10
CHANGE_ROWS_MAX = 40        # 每一類最多列幾檔
POLL_SECONDS = 30 * 60
WINDOW_START = 21 * 60      # 交易日 21:00–23:30 每 30 分鐘看一次鏡像（排程主機 21:05 抓完會先戳一次）
WINDOW_END = 23 * 60 + 30

_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "lastRunAt": None, "lastError": None, "lastAdded": [], "latest": None}


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS etf_snapshots (
            etf_code TEXT NOT NULL, trade_date TEXT NOT NULL, name TEXT, issuer TEXT, source TEXT, nav REAL, units REAL, rows INTEGER NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY (etf_code, trade_date)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS etf_holdings (
            etf_code TEXT NOT NULL, trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT, shares INTEGER NOT NULL, weight_pct REAL,
            PRIMARY KEY (etf_code, trade_date, stock_code)
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_etf_holdings_date ON etf_holdings (trade_date)")


def _int(value: Any) -> int | None:
    try:
        return int(round(float(str(value).replace(",", "").strip())))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def parse_mirror(payload: Any) -> tuple[str | None, dict[str, dict[str, Any]]]:
    """鏡像檔：{"date":"2026-09-24","etfs":{"00981A":{"name","issuer","source","nav","units","rows":[[代號,名稱,股數,權重%],...]}}}"""
    if not isinstance(payload, dict):
        return None, {}
    date = str(payload.get("date") or "")[:10]
    if len(date) != 10:
        return None, {}
    out: dict[str, dict[str, Any]] = {}
    for code, item in (payload.get("etfs") or {}).items():
        code = str(code).strip().upper()
        if not isinstance(item, dict):
            continue
        rows: list[tuple[str, str, int, float | None]] = []
        for row in item.get("rows") or []:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            stock = str(row[0]).strip().upper()
            shares = _int(row[2])
            if not stock or shares is None or shares < 0:
                continue
            rows.append((stock, str(row[1] or "").strip(), shares, _float(row[3]) if len(row) > 3 else None))
        if not rows:
            continue
        name, issuer = ETF_META.get(code, (str(item.get("name") or code), str(item.get("issuer") or "")))
        out[code] = {"name": str(item.get("name") or name), "issuer": str(item.get("issuer") or issuer), "source": str(item.get("source") or ""),
                     "nav": _float(item.get("nav")), "units": _float(item.get("units")), "rows": rows}
    return date, out


def save_snapshot(date: str, code: str, item: dict[str, Any]) -> int:
    initialize_database()
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.execute("DELETE FROM etf_holdings WHERE etf_code = ? AND trade_date = ?", (code, date))
        connection.executemany(
            "INSERT OR REPLACE INTO etf_holdings (etf_code, trade_date, stock_code, stock_name, shares, weight_pct) VALUES (?, ?, ?, ?, ?, ?)",
            [(code, date, s, n, sh, w) for s, n, sh, w in item["rows"]],
        )
        connection.execute(
            "INSERT OR REPLACE INTO etf_snapshots (etf_code, trade_date, name, issuer, source, nav, units, rows, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, date, item.get("name"), item.get("issuer"), item.get("source"), item.get("nav"), item.get("units"), len(item["rows"]), now),
        )
    return len(item["rows"])


def dates(limit: int = 30) -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT DISTINCT trade_date FROM etf_snapshots ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()
    return [str(r["trade_date"]) for r in rows]


def stored_codes(date: str) -> set[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT etf_code FROM etf_snapshots WHERE trade_date = ?", (date,)).fetchall()
    return {str(r["etf_code"]) for r in rows}


def snapshot(code: str, date: str) -> dict[str, Any] | None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT * FROM etf_snapshots WHERE etf_code = ? AND trade_date = ?", (code, date)).fetchone()
    return dict(row) if row else None


def holdings(code: str, date: str) -> dict[str, dict[str, Any]]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT stock_code, stock_name, shares, weight_pct FROM etf_holdings WHERE etf_code = ? AND trade_date = ?", (code, date)).fetchall()
    return {str(r["stock_code"]): {"name": r["stock_name"], "shares": int(r["shares"]), "weight": r["weight_pct"]} for r in rows}


def prev_date(code: str, date: str) -> str | None:
    """這一檔在 date 之前最近的一份（最多隔 PREV_MAX_GAP_DAYS 個日曆日）。"""
    floor = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=PREV_MAX_GAP_DAYS)).strftime("%Y-%m-%d")
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT trade_date FROM etf_snapshots WHERE etf_code = ? AND trade_date < ? AND trade_date >= ? ORDER BY trade_date DESC LIMIT 1", (code, date, floor)).fetchone()
    return str(row["trade_date"]) if row else None


def _stock_info(stock: str, fallback_name: str | None) -> tuple[str, str]:
    group, name = group_and_name(stock)
    if name == stock:      # 不在族群名單裡：用投信給的名稱
        name = fallback_name or stock
    return group, name


def _row(stock: str, cur: dict[str, Any] | None, prev: dict[str, Any] | None) -> dict[str, Any]:
    cur_sh = cur["shares"] if cur else 0
    prev_sh = prev["shares"] if prev else 0
    group, name = _stock_info(stock, (cur or prev or {}).get("name"))
    return {
        "code": stock, "name": name, "group": group,
        "prevShares": prev_sh, "shares": cur_sh, "deltaShares": cur_sh - prev_sh, "deltaLots": round((cur_sh - prev_sh) / 1000, 1),
        "weight": cur["weight"] if cur else None, "prevWeight": prev["weight"] if prev else None,
    }


def etf_changes(code: str, date: str) -> dict[str, Any] | None:
    """一檔 ETF 在 date 跟前一份的差：新增／加碼／減碼／刪除＋前十大持股。"""
    meta = snapshot(code, date)
    if not meta:
        return None
    cur = holdings(code, date)
    pdate = prev_date(code, date)
    prev = holdings(code, pdate) if pdate else {}
    new, inc, dec, removed = [], [], [], []
    if pdate:
        for stock, c in cur.items():
            p = prev.get(stock)
            if p is None:
                new.append(_row(stock, c, None))
            elif c["shares"] > p["shares"]:
                inc.append(_row(stock, c, p))
            elif c["shares"] < p["shares"]:
                dec.append(_row(stock, c, p))
        for stock, p in prev.items():
            if stock not in cur:
                removed.append(_row(stock, None, p))
    key = lambda r: -abs(r["deltaShares"])  # noqa: E731
    for lst in (new, inc, dec, removed):
        lst.sort(key=key)
    top = sorted(cur.items(), key=lambda kv: -(kv[1]["weight"] or 0))[:TOP_HOLDINGS]
    name, issuer = ETF_META.get(code, (meta.get("name") or code, meta.get("issuer") or ""))
    pmeta = snapshot(code, pdate) if pdate else None
    return {
        "code": code, "name": meta.get("name") or name, "issuer": meta.get("issuer") or issuer, "source": meta.get("source"),
        "date": date, "prevDate": pdate, "holdings": len(cur), "prevHoldings": len(prev) if pdate else None,
        "nav": meta.get("nav"), "units": meta.get("units"), "unitsDelta": (meta.get("units") - pmeta.get("units")) if (pmeta and meta.get("units") is not None and pmeta.get("units") is not None) else None,
        "counts": {"new": len(new), "increased": len(inc), "decreased": len(dec), "removed": len(removed), "unchanged": (len(cur) - len(new) - len(inc) - len(dec)) if pdate else None},
        "new": new[:CHANGE_ROWS_MAX], "increased": inc[:CHANGE_ROWS_MAX], "decreased": dec[:CHANGE_ROWS_MAX], "removed": removed[:CHANGE_ROWS_MAX],
        "top": [{"code": s, "name": _stock_info(s, v["name"])[1], "group": _stock_info(s, v["name"])[0], "shares": v["shares"], "weight": v["weight"]} for s, v in top],
    }


def sync_moves(etfs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """五檔同步：同一檔股票被 ≥SYNC_MIN_ETFS 檔 ETF 加碼（含新增）或減碼（含刪除）。"""
    agg: dict[str, dict[str, dict[str, Any]]] = {"buy": {}, "sell": {}}
    for e in etfs:
        if not e or not e.get("prevDate"):
            continue
        for side, keys in (("buy", ("new", "increased")), ("sell", ("decreased", "removed"))):
            for k in keys:
                for r in e.get(k) or []:
                    a = agg[side].setdefault(r["code"], {"code": r["code"], "name": r["name"], "group": r["group"], "etfs": [], "deltaLots": 0.0})
                    a["etfs"].append({"code": e["code"], "name": e["name"], "deltaLots": r["deltaLots"], "kind": k})
                    a["deltaLots"] = round(a["deltaLots"] + r["deltaLots"], 1)
    out: dict[str, list[dict[str, Any]]] = {}
    for side, items in agg.items():
        rows = [{**a, "count": len(a["etfs"])} for a in items.values() if len(a["etfs"]) >= SYNC_MIN_ETFS]
        rows.sort(key=lambda a: (-a["count"], -abs(a["deltaLots"])))
        out[side] = rows[:CHANGE_ROWS_MAX]
    return out


def report_section(as_of: str | None = None) -> dict[str, Any] | None:
    """下午報用：基準日（含）以前最新一天的五檔持股變化；沒有資料回 None。"""
    have = dates(40)
    if as_of:
        have = [d for d in have if d <= as_of]
    if not have:
        return None
    date = have[0]
    etfs = [c for c in etf_changes_all(date)]
    return {
        "date": date, "etfs": etfs, "missing": [code for code, _n, _i in ETFS if code not in {e["code"] for e in etfs}],
        "sync": sync_moves(etfs),
        "withPrev": sum(1 for e in etfs if e.get("prevDate")),
    }


def etf_changes_all(date: str) -> list[dict[str, Any]]:
    out = []
    for code, _name, _issuer in ETFS:
        c = etf_changes(code, date)
        if c:
            out.append(c)
    return out


# ---- 收集器：從鏡像拉回來 ----

def collect_once(fetcher: Callable[[str], Any] | None = None, *, limit: int = FETCH_LIMIT) -> dict[str, Any]:
    fetch = fetcher or _default_fetcher
    result: dict[str, Any] = {"added": [], "checked": 0, "errors": []}
    index = fetch(_mirror_url(MIRROR_INDEX, volatile=True))
    wanted = sorted({str(d) for d in index if isinstance(d, str) and len(d) == 10}, reverse=True)[:limit] if isinstance(index, list) else []
    for i, day in enumerate(wanted):
        have = stored_codes(day)
        if len(have) >= len(ETFS):
            continue
        result["checked"] += 1
        try:
            date, etfs = parse_mirror(fetch(_mirror_url(f"etf-{day}.json", volatile=i < 2)))
        except urllib.error.HTTPError as exc:
            result["errors"].append(f"{day}: HTTP {exc.code}")
            continue
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{day}: {type(exc).__name__}: {exc}")
            continue
        if date != day:
            result["errors"].append(f"{day}: 檔案日期 {date} 不符")
            continue
        for code, item in etfs.items():
            if code not in have:
                save_snapshot(day, code, item)
                result["added"].append(f"{day}:{code}")
    with _lock:
        _state["lastRunAt"] = datetime.now(TW_TZ).isoformat(timespec="seconds")
        _state["lastError"] = "; ".join(result["errors"]) or None
        _state["lastAdded"] = result["added"]
        _state["latest"] = (dates(1) or [None])[0]
    return result


def run_collect(*, only_if_new: bool = False) -> dict[str, Any]:
    """抓一輪；有新的快照就重算下午報（手動戳一定重算）。"""
    result = collect_once()
    if only_if_new and not result["added"]:
        result["swingRefreshed"] = False
        return result
    try:
        from swing_report import run_once as run_swing_report

        run_swing_report()
        result["swingRefreshed"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("swing report refresh after etf holdings failed: %s", exc)
        result["swingRefreshed"] = False
    return result


def _loop() -> None:
    time.sleep(150)
    try:
        run_collect(only_if_new=True)
    except Exception:  # noqa: BLE001
        logger.exception("etf holdings collect failed")
    while True:
        time.sleep(POLL_SECONDS)
        now = datetime.now(TW_TZ)
        minute = now.hour * 60 + now.minute
        if is_trading_day(now) and WINDOW_START <= minute <= WINDOW_END:
            try:
                run_collect(only_if_new=True)
            except Exception:  # noqa: BLE001
                logger.exception("etf holdings collect failed")


def start_etf_collector() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_loop, name="etf-holdings", daemon=True).start()
    return True


def collector_status() -> dict[str, Any]:
    with _lock:
        state = dict(_state)
    return {**state, "etfs": [{"code": c, "name": n, "issuer": i} for c, n, i in ETFS], "dates": dates(10)}
