"""個股月營收圖（2026-11-23 使用者：我們也要做出這種個股的營收圖——月營收柱＋月底股價、單季三率、月增、年增，右上基本面／技術面分數）。

資料：
- 月營收：FinMind TaiwanStockMonthRevenue（一檔一次抓近 4 年，點開才抓，存 revenue_chart_cache，6 小時內不重抓）；
  我們自己的營收彙總表（revenue_month，觀測站鏡像）有比較新的月份就補上去。年增、月增用營收自己算（跟觀測站一樣）。
- 月底收盤：FinMind TaiwanStockPrice 每個月最後一個交易日的收盤（未還原，跟對方的圖一樣）。
- 單季毛利率／營益率／淨利率：我們的季報彙總表（financials.all_singles）。
分數（各 100 分，數字越大越好）：
- 基本面＝（年增分＋月增分＋利益率分）÷ 3：
  - 年增分＝最近 3 個月年增平均在全市場排第幾（百分位）；月增分＝最近 3 個月月增平均的百分位；
  - 利益率分＝（最新一季三率平均的百分位＋三率平均比去年同季變化的百分位）÷ 2（水準跟改善都算）。
- 技術面＝（基期分＋均線分數 × 100 ÷ 15）÷ 2：
  - 基期分＝100 ×（1 −（收盤 − 一年最低）÷（一年最高 − 一年最低）），還原價、近 240 個交易日；越接近一年低點越高分（低基期）；
  - 均線分數＝全站同一套 score2（滿分 15）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database

logger = logging.getLogger("hanstock.revenue_chart")
TW_TZ = timezone(timedelta(hours=8))
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
MONTHS = 34                  # 圖上幾個月（對方的圖 2024/01～2026/10）
HISTORY_MONTHS = MONTHS + 13  # 多抓 13 個月才算得出第一個月的年增
REFRESH_SECONDS = 6 * 3600
BASE_DAYS = 240
SCORE_TTL = 3600

_lock = threading.Lock()
_scores: dict[str, Any] = {"at": 0.0, "key": None, "data": None}


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS revenue_chart_cache (
            code TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            revenue TEXT NOT NULL,
            closes TEXT NOT NULL
        )"""
    )


def _finmind(dataset: str, code: str, start: str) -> Any:
    params = {"dataset": dataset, "data_id": code, "start_date": start}
    token = os.getenv("FINMIND_TOKEN", "").strip()

    def call(extra: dict[str, str]) -> Any:
        url = FINMIND_URL + "?" + urllib.parse.urlencode({**params, **extra})
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)"})
        with urllib.request.urlopen(request, timeout=40) as response:  # noqa: S310
            return json.load(response)

    if token:
        try:
            return call({"token": token})
        except Exception:  # noqa: BLE001  token 有問題就改免登入
            logger.info("FinMind with token failed, retry without", exc_info=True)
    return call({})


def _ym_add(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) - 1 + n
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def parse_revenue(payload: Any) -> dict[str, float]:
    """{YYYY-MM: 營收（元）}"""
    out: dict[str, float] = {}
    for r in (payload or {}).get("data") or []:
        try:
            ym = f"{int(r['revenue_year'])}-{int(r['revenue_month']):02d}"
            out[ym] = float(r["revenue"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def parse_month_closes(payload: Any) -> dict[str, float]:
    """{YYYY-MM: 那個月最後一個交易日收盤}"""
    last: dict[str, tuple[str, float]] = {}
    for r in (payload or {}).get("data") or []:
        d, c = str(r.get("date") or ""), r.get("close")
        if len(d) >= 7 and c:
            ym = d[:7]
            if ym not in last or d > last[ym][0]:
                last[ym] = (d, float(c))
    return {ym: v[1] for ym, v in last.items()}


def _cached_history(code: str, now: datetime, fetcher: Callable[[str, str, str], Any] | None) -> tuple[dict[str, float], dict[str, float], str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT * FROM revenue_chart_cache WHERE code = ?", (code,)).fetchone()
    if row:
        age = (now - datetime.fromisoformat(row["fetched_at"])).total_seconds()
        if age < REFRESH_SECONDS:
            return json.loads(row["revenue"]), json.loads(row["closes"]), row["fetched_at"]
    call = fetcher or _finmind
    start_ym = _ym_add(now.strftime("%Y-%m"), -HISTORY_MONTHS)
    try:
        revenue = parse_revenue(call("TaiwanStockMonthRevenue", code, start_ym + "-01"))
        closes = parse_month_closes(call("TaiwanStockPrice", code, _ym_add(start_ym, 12) + "-01"))
    except Exception:  # noqa: BLE001  抓不到就用舊的
        logger.exception("FinMind 個股營收圖抓不到 %s", code)
        if row:
            return json.loads(row["revenue"]), json.loads(row["closes"]), row["fetched_at"]
        raise
    stamp = now.isoformat(timespec="seconds")
    if revenue:
        with get_connection() as connection:
            _schema(connection)
            connection.execute("INSERT OR REPLACE INTO revenue_chart_cache (code, fetched_at, revenue, closes) VALUES (?, ?, ?, ?)",
                               (code, stamp, json.dumps(revenue), json.dumps(closes)))
    return revenue, closes, stamp


def _own_revenue(code: str) -> tuple[dict[str, float], dict[str, Any]]:
    """我們的營收彙總表（觀測站鏡像，千元）：{YYYY-MM: 元}、最新那列的名稱／市場／產業。"""
    from revenue_rank import _schema as revenue_schema

    with get_connection() as connection:
        revenue_schema(connection)
        rows = connection.execute("SELECT ym, rev, name, market, industry FROM revenue_month WHERE code = ? ORDER BY ym DESC", (code,)).fetchall()
    info = dict(rows[0]) if rows else {}
    return {str(r["ym"]): float(r["rev"]) * 1000 for r in rows if r["rev"] is not None}, info


# ------------------------------------------------------------------ 分數（全市場百分位）

def _pct_map(values: dict[str, float]) -> dict[str, float]:
    """{代號: 0～100 百分位}（數字越大排越前面；同值給一樣的分）。"""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    if n < 2:
        return {k: 50.0 for k in values}
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        p = (i + j) / 2 / (n - 1) * 100
        for k in range(i, j + 1):
            out[items[k][0]] = p
        i = j + 1
    return out


def _avg(values: list[float | None]) -> float | None:
    got = [v for v in values if v is not None]
    return sum(got) / len(got) if got else None


def market_scores() -> dict[str, Any]:
    """全市場的年增、月增、利益率百分位（營收、財報有更新才重算；最多 1 小時算一次）。"""
    from financials import all_singles
    from revenue_rank import _schema as revenue_schema

    with get_connection() as connection:
        revenue_schema(connection)
        key_row = connection.execute("SELECT COUNT(*) AS n, MAX(ym) AS m, MAX(first_seen) AS f FROM revenue_month").fetchone()
        key = (key_row["n"], key_row["m"], key_row["f"])
        with _lock:
            if _scores["data"] is not None and _scores["key"] == key and time.monotonic() - _scores["at"] < SCORE_TTL:
                return _scores["data"]
        months = [r["ym"] for r in connection.execute("SELECT DISTINCT ym FROM revenue_month ORDER BY ym DESC LIMIT 4").fetchall()]
        rows = connection.execute(
            f"SELECT code, ym, yoy, mom FROM revenue_month WHERE ym IN ({','.join('?' for _ in months)}) ORDER BY ym DESC", tuple(months)
        ).fetchall() if months else []
    per: dict[str, list[tuple[float | None, float | None]]] = {}
    for r in rows:
        lst = per.setdefault(str(r["code"]).upper(), [])
        if len(lst) < 3:
            lst.append((r["yoy"], r["mom"]))
    yoy = {c: v for c, v in ((c, _avg([x[0] for x in l])) for c, l in per.items()) if v is not None}
    mom = {c: v for c, v in ((c, _avg([x[1] for x in l])) for c, l in per.items()) if v is not None}
    level, change = {}, {}
    for code, singles in all_singles().items():
        if not singles:
            continue
        now = singles[0]
        avg_now = _avg([now.get("gm"), now.get("margin"), now.get("nm")])
        if avg_now is None:
            continue
        level[code] = avg_now
        last_year = next((s for s in singles if s["season"] == f"{int(now['season'][:4]) - 1}{now['season'][4:]}"), None)
        if last_year:
            avg_ly = _avg([last_year.get("gm") if now.get("gm") is not None else None, last_year.get("margin") if now.get("margin") is not None else None,
                           last_year.get("nm") if now.get("nm") is not None else None])
            if avg_ly is not None:
                change[code] = avg_now - avg_ly
    data = {"yoy": _pct_map(yoy), "mom": _pct_map(mom), "level": _pct_map(level), "change": _pct_map(change), "months": months}
    with _lock:
        _scores.update({"at": time.monotonic(), "key": key, "data": data})
    return data


def technical(code: str) -> dict[str, Any] | None:
    """基期分（近 240 個交易日還原收盤的位置，越低分數越高）、均線分數（score2）。"""
    from heilong_backtest import _schema as heilong_schema

    with get_connection() as connection:
        heilong_schema(connection)
        rows = connection.execute(
            "SELECT trade_date, close, score2 FROM heilong_daily WHERE stock_code = ? ORDER BY trade_date DESC LIMIT ?", (code, BASE_DAYS)
        ).fetchall()
    if not rows or rows[0]["score2"] is None:
        return None
    closes = [float(r["close"]) for r in rows if r["close"]]
    hi, lo, last = max(closes), min(closes), closes[0]
    base = round(100 * (1 - (last - lo) / (hi - lo))) if hi > lo else 50
    score2 = int(rows[0]["score2"])
    return {"score": round((base + score2 * 100 / 15) / 2), "base": base, "score2": score2, "date": str(rows[0]["trade_date"]),
            "high": hi, "low": lo, "days": len(closes)}


# ------------------------------------------------------------------ 圖

def chart(code: str, *, now: datetime | None = None, fetcher: Callable[[str, str, str], Any] | None = None) -> dict[str, Any]:
    from financials import all_singles

    code = str(code or "").strip().upper()
    now = now or datetime.now(TW_TZ)
    if not code:
        return {"status": "none", "code": code}
    try:
        revenue, closes, fetched = _cached_history(code, now, fetcher)
    except Exception:  # noqa: BLE001  FinMind 抓不到、也沒存過：只畫我們自己有的那幾個月
        revenue, closes, fetched = {}, {}, None
    own, info = _own_revenue(code)
    for ym, rev in own.items():           # 鏡像比 FinMind 新的月份補上去（FinMind 有的照 FinMind）
        revenue.setdefault(ym, rev)
    if not revenue:
        return {"status": "none", "code": code, "message": "查不到這檔的月營收"}
    latest = max(revenue)
    yms = [_ym_add(latest, -i) for i in range(MONTHS - 1, -1, -1)]
    months = []
    for ym in yms:
        rev, prev, last_year = revenue.get(ym), revenue.get(_ym_add(ym, -1)), revenue.get(_ym_add(ym, -12))
        months.append({
            "ym": ym, "rev": round(rev / 1e8, 2) if rev is not None else None,
            "mom": round((rev / prev - 1) * 100, 1) if rev and prev else None,
            "yoy": round((rev / last_year - 1) * 100, 1) if rev and last_year else None,
            "close": closes.get(ym),
        })
    for i in range(1, len(months)):
        a, b = months[i]["yoy"], months[i - 1]["yoy"]
        months[i]["yoyMove"] = round(a - b, 1) if a is not None and b is not None else None
    quarter_end = {"Q1": "03", "Q2": "06", "Q3": "09", "Q4": "12"}
    quarters = [{"season": s["season"], "label": s.get("label"), "ym": s["season"][:4] + "-" + quarter_end.get(s["season"][4:], "12"),
                 "gm": s.get("gm"), "om": s.get("margin"), "nm": s.get("nm")}
                for s in reversed(all_singles().get(code) or []) if s["season"][:4] + "-" + quarter_end.get(s["season"][4:], "12") >= yms[0]]
    ms = market_scores()
    yoy_s, mom_s = ms["yoy"].get(code), ms["mom"].get(code)
    lvl, chg = ms["level"].get(code), ms["change"].get(code)
    margin_s = _avg([lvl, chg])
    parts = [p for p in (yoy_s, mom_s, margin_s) if p is not None]
    fundamental = {"score": round(sum(parts) / len(parts)) if parts else None,
                   "yoy": round(yoy_s) if yoy_s is not None else None, "mom": round(mom_s) if mom_s is not None else None,
                   "margin": round(margin_s) if margin_s is not None else None}
    from brew_launch_history import group_and_name

    group, gname = group_and_name(code)
    return {
        "status": "ok", "code": code, "name": info.get("name") or (gname if gname != code else code), "market": info.get("market"),
        "industry": info.get("industry"), "group": group, "latest": latest, "months": months, "quarters": quarters,
        "recent": {"yoy": [m["yoy"] for m in months[-3:]], "mom": [m["mom"] for m in months[-3:]]},
        "scores": {"fundamental": fundamental, "technical": technical(code)},
        "fetchedAt": fetched,
    }
