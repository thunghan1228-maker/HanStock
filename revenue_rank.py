"""每月營收成長榜（2026-10-09 使用者：照莊爸「每月營收成長榜 學員版」一模一樣做到我們網站）。

資料：
- 營收：公開資訊觀測站「每月營業收入彙總表」（上市、上櫃，國內＋國外 KY）。觀測站擋正式站主機，走 tw-groups data 分支鏡像
  tpex/revenue-mops-YYYY-MM.json（排程主機每天 12:30／18:30／23:30 抓）。證交所／櫃買的開放資料要到月中才換月
  （10/09 還停在 8 月營收），不能拿來看當月陸續公布的狀況。
- 公布日：觀測站沒有每家公司的公布日期，跟莊爸一樣用「我們第一次抓到的時間」（tpex/revenue-seen-YYYY-MM.json，
  只增不改）。每個月第一次抓的那批（baseline）公布日不確定——除非那次就是次月 1 號（營收不可能更早公布）。
  2026/09 營收從 10/09 才開始記，10/09 以前公布的標「10/09 前」，不算隔日統計。半夜 0～3 點才跑完的那輪算前一天。
- 收盤、成交量、隔日漲跌：我們的日K（證交所 MI_INDEX 全市場、櫃買開放資料）。隔日＝公布日之後第一個交易日的收盤
  vs 公布日（遇假日用之前最近一個交易日）收盤；公司可能盤中或盤後公布，一律這樣算（跟莊爸同）。
- 🚀 加速／🐢 放緩：年增率比上個月的年增率多／少 10 個百分點以上；⚠️：年增或累計年增為負。

頁面各段（族群分析、多觀察、火箭烏龜、懸賞榜、總表）在前端用 rows 算；跨月份的「歷月統計」在後端算。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from chips_daily import _default_fetcher, _mirror_url
from database import get_connection, initialize_database

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))

MIRROR_INDEX = "revenue-index.json"
KEEP_MONTHS = 13            # 鏡像裡最多拉幾個月
POLL_SECONDS = 20 * 60      # 每 20 分鐘看鏡像有沒有更新（排程主機抓完也會直接戳）
ACCEL_POINTS = 10.0         # 年增比上月多／少 10 個百分點以上＝加速／放緩
LATE_RUN_HOURS = 3          # 23:30 那輪拖到半夜才跑完，算前一天
FIELDS = ["code", "name", "market", "yoy", "mom", "cumYoy", "revYi", "close", "volume", "announce", "known",
          "prevYoy", "next", "industry", "note"]
MARKET_LABEL = {"TSE": "上市", "OTC": "上櫃"}

_state: dict[str, Any] = {"lastCollect": None, "lastError": None, "result": None, "indexUpdated": None}
_cache: dict[str, Any] = {}
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


# ------------------------------------------------------------------ 儲存

def _schema(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS revenue_month (
            code TEXT NOT NULL, ym TEXT NOT NULL, market TEXT NOT NULL, name TEXT NOT NULL, industry TEXT,
            rev REAL, prev_rev REAL, last_year_rev REAL, mom REAL, yoy REAL, cum_rev REAL, cum_last REAL, cum_yoy REAL,
            note TEXT, first_seen TEXT, PRIMARY KEY (code, ym)
        );
        CREATE INDEX IF NOT EXISTS revenue_month_ym_idx ON revenue_month (ym);
        CREATE TABLE IF NOT EXISTS revenue_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )


def _meta_get(key: str) -> Any:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT value FROM revenue_meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def _meta_set(key: str, value: Any) -> None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.execute("INSERT OR REPLACE INTO revenue_meta (key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))


def parse_mirror(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """鏡像 {month, fetched, published, fields, rows:[[code,name,market,industry,rev,prevRev,lastYearRev,momPct,yoyPct,cumRev,
    cumLastYearRev,cumPct,note]]}。"""
    if not isinstance(payload, dict):
        return None, []
    month = str(payload.get("month") or "")[:7] or None
    fields = payload.get("fields") or []
    out = []
    for row in payload.get("rows") or []:
        if not isinstance(row, list) or len(row) < len(fields):
            continue
        item = dict(zip(fields, row))
        code = str(item.get("code") or "").strip().upper()
        if not code or item.get("market") not in MARKET_LABEL:
            continue
        out.append(item | {"code": code})
    return month, out


def save_month(month: str, rows: list[dict[str, Any]], first_seen: dict[str, str]) -> int:
    if not rows:
        return 0
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            """INSERT INTO revenue_month (code, ym, market, name, industry, rev, prev_rev, last_year_rev, mom, yoy, cum_rev,
                   cum_last, cum_yoy, note, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(code, ym) DO UPDATE SET market = excluded.market, name = excluded.name, industry = excluded.industry,
                   rev = excluded.rev, prev_rev = excluded.prev_rev, last_year_rev = excluded.last_year_rev, mom = excluded.mom,
                   yoy = excluded.yoy, cum_rev = excluded.cum_rev, cum_last = excluded.cum_last, cum_yoy = excluded.cum_yoy,
                   note = excluded.note, first_seen = COALESCE(revenue_month.first_seen, excluded.first_seen)""",
            [(r["code"], month, r["market"], str(r.get("name") or ""), r.get("industry"), r.get("rev"), r.get("prevRev"),
              r.get("lastYearRev"), r.get("momPct"), r.get("yoyPct"), r.get("cumRev"), r.get("cumLastYearRev"), r.get("cumPct"),
              r.get("note"), first_seen.get(r["code"])) for r in rows],
        )
    return len(rows)


def load_month(month: str) -> list[dict[str, Any]]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT * FROM revenue_month WHERE ym = ?", (month,)).fetchall()
    return [dict(r) for r in rows]


def load_months() -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT DISTINCT ym FROM revenue_month ORDER BY ym DESC").fetchall()
    return [r["ym"] for r in rows]


# ------------------------------------------------------------------ 抓資料

def _now() -> datetime:
    return datetime.now(TW_TZ)


def collect(now: datetime | None = None, fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    now = now or _now()
    call = fetcher or _default_fetcher
    result: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "months": {}, "errors": []}
    index = call(_mirror_url(MIRROR_INDEX, volatile=True))
    months = [str(m) for m in (index.get("months") if isinstance(index, dict) else None) or []][:KEEP_MONTHS]
    for month in months:
        try:
            mirror = call(_mirror_url(f"revenue-mops-{month}.json", volatile=True))
            got, rows = parse_mirror(mirror)
            if got != month:
                raise RuntimeError(f"鏡像月份 {got} 不是 {month}")
            try:
                seen = call(_mirror_url(f"revenue-seen-{month}.json", volatile=True))
            except Exception:  # noqa: BLE001  以前的月份沒有記公布日
                seen = None
            first = (seen or {}).get("first") or {}
            saved = save_month(month, rows, first)
            meta = {"fetched": mirror.get("fetched"), "published": mirror.get("published"),
                    "baseline": (seen or {}).get("baseline"), "count": saved}
            _meta_set(f"month:{month}", meta)
            result["months"][month] = saved
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{month}: {type(exc).__name__}: {exc}")
    with _lock:
        _state["lastCollect"] = result["at"]
        _state["lastError"] = "; ".join(result["errors"]) or None
        _state["result"] = {k: v for k, v in result.items() if k != "errors"}
        _state["indexUpdated"] = index.get("updated") if isinstance(index, dict) else None
        _cache.clear()
    return result


# ------------------------------------------------------------------ 計算

def _next_month_first(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year + (mon == 12):04d}-{mon % 12 + 1:02d}-01"


def _prev_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year - (mon == 1):04d}-{(mon - 2) % 12 + 1:02d}"


def announce_day(first_seen: str | None, baseline: str | None, month: str) -> tuple[str | None, bool]:
    """(公布日, 確定嗎)。第一次抓到的時間減 3 小時取日期（半夜才跑完的那輪算前一天）；
    跟這個月第一次抓的那批（baseline）同時出現的不確定，除非那次已經是次月 1 號。"""
    if not first_seen:
        return None, False
    try:
        seen = datetime.fromisoformat(first_seen)
    except ValueError:
        return None, False
    day = (seen - timedelta(hours=LATE_RUN_HOURS)).date().isoformat()
    if first_seen == baseline and day != _next_month_first(month):
        return day, False
    return day, True


def _closes(codes: list[str], since: str) -> dict[str, list[tuple[str, float, int]]]:
    """{代號: [(日期, 收盤, 成交量張), ...]}，由舊到新。"""
    out: dict[str, list[tuple[str, float, int]]] = {}
    if not codes:
        return out
    initialize_database()
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"""SELECT stock_code, substr(bar_time, 1, 10) AS d, close, volume FROM bars_1d
                    WHERE bar_time >= ? AND stock_code IN ({','.join('?' for _ in batch)}) ORDER BY bar_time""",
                (since, *batch),
            ).fetchall()
            for r in rows:
                out.setdefault(str(r["stock_code"]).upper(), []).append((r["d"], float(r["close"]), int(r["volume"] or 0)))
    return out


def next_day_change(series: list[tuple[str, float, int]], day: str) -> float | None:
    """公布日之後第一個交易日收盤 vs 公布日（或之前最近一個交易日）收盤，%。"""
    base = None
    for d, close, _vol in series:
        if d <= day:
            base = close
        elif base:
            return round((close / base - 1) * 100, 2)
        else:
            return None
    return None


def _bucket() -> dict[str, float]:
    return {"n": 0, "up": 0, "sum": 0.0}


def _add(bucket: dict[str, float], change: float) -> None:
    bucket["n"] += 1
    bucket["up"] += 1 if change > 0 else 0
    bucket["sum"] = round(bucket["sum"] + change, 4)


def month_rows(month: str) -> tuple[list[list[Any]], dict[str, Any]]:
    rows = load_month(month)
    meta = _meta_get(f"month:{month}") or {}
    prev = {r["code"]: r["yoy"] for r in load_month(_prev_month(month))}
    since = (date.fromisoformat(_next_month_first(month)) - timedelta(days=12)).isoformat()
    series = _closes(sorted({r["code"] for r in rows}), since)
    out = []
    for r in rows:
        day, known = announce_day(r["first_seen"], meta.get("baseline"), month)
        s = series.get(r["code"], [])
        close, volume = (s[-1][1], s[-1][2]) if s else (None, None)
        change = next_day_change(s, day) if day and known else None
        out.append([r["code"], r["name"], MARKET_LABEL.get(r["market"], r["market"]), r["yoy"], r["mom"], r["cum_yoy"],
                    round(r["rev"] / 100_000, 2) if r["rev"] is not None else None, close, volume, day, known,
                    prev.get(r["code"]), change, r["industry"], r["note"]])
    price_date = max((v[-1][0] for v in series.values() if v), default=None)
    return out, {"baseline": meta.get("baseline"), "priceDate": price_date, "fetched": meta.get("fetched"),
                 "published": meta.get("published")}


def _flags(yoy: float | None, prev_yoy: float | None, cum: float | None) -> tuple[bool, bool, bool]:
    accel = yoy is not None and prev_yoy is not None and yoy - prev_yoy >= ACCEL_POINTS
    decel = yoy is not None and prev_yoy is not None and yoy - prev_yoy <= -ACCEL_POINTS
    warn = (yoy is not None and yoy < 0) or (cum is not None and cum < 0)
    return accel, decel, warn


def month_stats(rows: list[list[Any]]) -> dict[str, dict[str, float]]:
    """全市場：公布後隔日漲跌，依標記分組（加速／持平／放緩／年增≥100%／有⚠️／沒⚠️）。"""
    idx = {f: i for i, f in enumerate(FIELDS)}
    out = {k: _bucket() for k in ("all", "accel", "flat", "decel", "yoy100", "warn", "nowarn")}
    for row in rows:
        change = row[idx["next"]]
        if change is None:
            continue
        accel, decel, warn = _flags(row[idx["yoy"]], row[idx["prevYoy"]], row[idx["cumYoy"]])
        _add(out["all"], change)
        _add(out["accel"] if accel else out["decel"] if decel else out["flat"], change)
        if row[idx["yoy"]] is not None and row[idx["yoy"]] >= 100:
            _add(out["yoy100"], change)
        _add(out["warn"] if warn else out["nowarn"], change)
    return out


def build_payload(month: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    months = load_months()
    if not months:
        return {"status": "empty", "message": "營收資料還在抓，晚一點再看", "collector": collector_status()}
    month = month if month in months else months[0]
    key = (month, _state.get("lastCollect"), now.date().isoformat())
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
    rows, meta = month_rows(month)
    history = []
    for m in months:
        if m == month:
            stats = month_stats(rows)
        elif (_meta_get(f"month:{m}") or {}).get("baseline"):
            stats = month_stats(month_rows(m)[0])
        else:
            continue   # 那個月沒有記公布日（補抓的舊月份）
        if stats["all"]["n"]:
            history.append({"month": m, **stats})
    known_days = sorted({r[9] for r in rows if r[9] and r[10]})
    payload = {
        "status": "ok", "month": month, "months": months, "fields": FIELDS, "rows": rows,
        "counts": {"published": len(rows), "tse": sum(1 for r in rows if r[2] == "上市"), "otc": sum(1 for r in rows if r[2] == "上櫃"),
                   "positive": sum(1 for r in rows if r[3] is not None and r[3] > 0)},
        "baseline": meta["baseline"], "baselineDay": announce_day(meta["baseline"], None, month)[0] if meta["baseline"] else None,
        "announceDays": known_days, "latestDay": known_days[-1] if known_days else None, "priceDate": meta["priceDate"],
        "history": history, "fetched": meta["fetched"], "published": meta["published"],
        "updatedAt": _state.get("lastCollect"), "mirrorUpdated": _state.get("indexUpdated"),
    }
    with _lock:
        _cache[key] = (time.time(), payload)
    return payload


def stock_detail(code: str) -> dict[str, Any]:
    """查個股營收：每個月的年增、月增、累計年增、月營收、公布日、公布隔日漲跌。"""
    code = str(code or "").strip().upper()
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT * FROM revenue_month WHERE code = ? ORDER BY ym DESC", (code,)).fetchall()
    if not rows:
        return {"status": "none", "code": code}
    series = _closes([code], (date.fromisoformat(rows[-1]["ym"] + "-01") + timedelta(days=20)).isoformat()).get(code, [])
    months = []
    for r in rows:
        meta = _meta_get(f"month:{r['ym']}") or {}
        day, known = announce_day(r["first_seen"], meta.get("baseline"), r["ym"])
        months.append({"month": r["ym"], "yoy": r["yoy"], "mom": r["mom"], "cumYoy": r["cum_yoy"],
                       "revYi": round(r["rev"] / 100_000, 2) if r["rev"] is not None else None,
                       "announce": day, "known": known, "next": next_day_change(series, day) if day and known else None,
                       "note": r["note"]})
    for i, m in enumerate(months[:-1]):
        prev = months[i + 1]
        if prev["month"] == _prev_month(m["month"]) and m["yoy"] is not None and prev["yoy"] is not None:
            m["accel"] = round(m["yoy"] - prev["yoy"], 2)
    return {"status": "ok", "code": code, "name": rows[0]["name"], "market": MARKET_LABEL.get(rows[0]["market"], rows[0]["market"]),
            "industry": rows[0]["industry"], "close": series[-1][1] if series else None, "months": months}


# ------------------------------------------------------------------ 背景排程

def collector_status() -> dict[str, Any]:
    with _lock:
        return {"lastCollect": _state["lastCollect"], "lastError": _state["lastError"], "result": _state["result"],
                "mirrorUpdated": _state["indexUpdated"]}


def run_collect() -> dict[str, Any]:
    try:
        return collect()
    except Exception as exc:  # noqa: BLE001
        logger.exception("營收成長榜抓資料失敗")
        with _lock:
            _state["lastError"] = f"{type(exc).__name__}: {exc}"
        return {"error": str(exc)}


def _loop() -> None:
    run_collect()
    while True:
        time.sleep(POLL_SECONDS)
        try:   # 鏡像有更新才整份重拉
            index = _default_fetcher(_mirror_url(MIRROR_INDEX, volatile=True))
            if isinstance(index, dict) and index.get("updated") != _state.get("indexUpdated"):
                run_collect()
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _state["lastError"] = f"index: {type(exc).__name__}: {exc}"


def start_revenue_collector() -> bool:
    global _thread
    if _thread and _thread.is_alive():
        return False
    _thread = threading.Thread(target=_loop, name="revenue-rank", daemon=True)
    _thread.start()
    return True
