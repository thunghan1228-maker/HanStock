"""估值河流圖・本站版（2026-10-10 使用者：照莊爸 zhuang.tw/river 做，公式推不出來就先做本站版）。

分水嶺＝近 4 季 EPS × 同族群本益比中位數 —— 股價在分水嶺之下，代表比它的同族群便宜；之上偏貴（莊爸頁面的說法）。
近 4 季 EPS 用證交所／櫃買中心公布的本益比換算（收盤 ÷ 本益比）；同族群＝那檔在族群表的第一個族群，
族群內本益比在 0～200 倍之間的成員取中位數（不到 3 檔有本益比，或不在任何族群，就用全市場中位數）。
四區照莊爸的比例（他 31 檔的「距分水嶺％」全部對得上）：分水嶺 ×0.618／×0.8／×1／×1.2／×1.382 切出
跌破特價、特價、便宜、貴、昂貴、超昂貴。莊爸的分水嶺每檔各有一個參考本益比、而且跟著時間變，公式不公開，
所以本站的位置可能跟他差一格。
虧損（沒有本益比）或本益比超過 200 倍（獲利太薄）不畫河道。
河道歷史：每個存了本益比的日子算當時的近 4 季 EPS，乘上現在的族群本益比 —— 季內 EPS 不變所以河道是平的，
每季財報公布後跳一次（跟莊爸的圖一樣）。
"""

from __future__ import annotations

import statistics
import threading
import time
from typing import Any

from database import get_connection, initialize_database
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

RATIOS = (0.618, 0.8, 1.0, 1.2, 1.382)
ZONES = ("跌破特價", "特價", "便宜", "貴", "昂貴", "超昂貴")
PE_MAX = 200.0                  # 本益比超過這個＝獲利太薄，不畫
GROUP_MIN = 3                   # 族群至少幾檔有本益比才用族群中位數
HIDE_GROUPS = ("千元",)          # 價格帶分類，不當族群
MA10_MIN_SCORE = 10             # 「均線分數 ≥10 × 便宜區」
CHART_DAYS = 800                # 圖最多畫幾根日K（約三年）
CACHE_SECONDS = 600.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "data": None}


def zone_of(price: float, s: float) -> int:
    for i, ratio in enumerate(RATIOS):
        if price < s * ratio:
            return i
    return len(RATIOS)


def _groups_of() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, members in STOCK_GROUPS.items():
        if name in SPECIAL_GROUP_NAMES or name in HIDE_GROUPS:
            continue
        for code, _n in members:
            out.setdefault(str(code).strip().upper(), []).append(name)
    return out


def _names() -> dict[str, str]:
    names: dict[str, str] = {}
    with get_connection() as connection:
        for r in connection.execute("SELECT stock_code, stock_name FROM stocks"):
            if r["stock_name"]:
                names[str(r["stock_code"]).strip().upper()] = str(r["stock_name"]).strip()
    for members in STOCK_GROUPS.values():
        for code, name in members:
            names.setdefault(str(code).strip().upper(), str(name))
    return names


def _latest_pe() -> tuple[dict[str, dict[str, Any]], str | None]:
    """({代號: {pe, pbr, date}}, 最新日期)：上市、上櫃各取最近一天。"""
    from fundamentals_daily import _schema as _fund_schema

    out: dict[str, dict[str, Any]] = {}
    latest = None
    with get_connection() as connection:
        _fund_schema(connection)
        for market in ("TSE", "OTC"):
            row = connection.execute("SELECT MAX(trade_date) AS d FROM stock_pe_daily WHERE market = ?", (market,)).fetchone()
            if not row or not row["d"]:
                continue
            latest = max(latest or "", str(row["d"]))
            for r in connection.execute("SELECT stock_code, pe, pbr FROM stock_pe_daily WHERE market = ? AND trade_date = ?", (market, row["d"])):
                out[str(r["stock_code"]).strip().upper()] = {"pe": r["pe"], "pbr": r["pbr"], "date": str(row["d"]), "market": market}
    return out, latest


def _latest_closes() -> tuple[dict[str, float], dict[str, int | None], str | None]:
    """({代號: 收盤}, {代號: 均線分數}, 日期)：黑龍表最新一天（族群外的也有）。"""
    from heilong_backtest import _schema as _heilong_schema

    with get_connection() as connection:
        _heilong_schema(connection)
        row = connection.execute("SELECT MAX(trade_date) AS d FROM heilong_daily").fetchone()
        if not row or not row["d"]:
            return {}, {}, None
        rows = connection.execute("SELECT stock_code, close, score2 FROM heilong_daily WHERE trade_date = ?", (row["d"],)).fetchall()
    closes = {str(r["stock_code"]).strip().upper(): float(r["close"]) for r in rows}
    scores = {str(r["stock_code"]).strip().upper(): r["score2"] for r in rows}
    return closes, scores, str(row["d"])


def _valid_pe(pe: Any) -> float | None:
    try:
        value = float(pe)
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= PE_MAX else None


def _refs(pe: dict[str, dict[str, Any]]) -> tuple[dict[str, float], float | None]:
    """({族群: 本益比中位數}, 全市場中位數)。"""
    market_vals = [v for v in (_valid_pe(x["pe"]) for x in pe.values()) if v]
    market = round(statistics.median(market_vals), 2) if market_vals else None
    refs: dict[str, float] = {}
    for name, members in STOCK_GROUPS.items():
        if name in SPECIAL_GROUP_NAMES or name in HIDE_GROUPS:
            continue
        vals = [v for v in (_valid_pe((pe.get(str(c).strip().upper()) or {}).get("pe")) for c, _n in members) if v]
        if len(vals) >= GROUP_MIN:
            refs[name] = round(statistics.median(vals), 2)
    return refs, market


def _build() -> dict[str, Any]:
    pe, pe_date = _latest_pe()
    closes, scores, price_date = _latest_closes()
    refs, market = _refs(pe)
    groups = _groups_of()
    names = _names()
    rows: dict[str, dict[str, Any]] = {}
    for code, info in pe.items():
        close = closes.get(code)
        if close is None or not (len(code) == 4 and code.isdigit()):
            continue
        mine = groups.get(code, [])
        group = next((g for g in mine if g in refs), None)
        ref = refs.get(group) if group else market
        row: dict[str, Any] = {"code": code, "name": names.get(code, code), "group": "/".join(mine), "refGroup": group or "全市場",
                               "price": close, "priceDate": price_date, "pe": info["pe"], "pbr": info["pbr"], "peDate": info["date"],
                               "score": scores.get(code), "ref": ref}
        value = _valid_pe(info["pe"])
        if info["pbr"]:
            row["bps"] = round(close / float(info["pbr"]), 2)
        if value is None or not ref:
            row.update({"noVal": True, "why": "thin" if info["pe"] and float(info["pe"]) > PE_MAX else "loss"})
        else:
            eps4 = close / value
            s = eps4 * ref
            z = zone_of(close, s)
            row.update({"noVal": False, "eps4": round(eps4, 2), "s": round(s, 1), "bounds": [round(s * r, 1) for r in RATIOS],
                        "zone": z, "zoneName": ZONES[z], "dist": round((close / s - 1) * 100, 1)})
        rows[code] = row
    return {"rows": rows, "refs": refs, "market": market, "peDate": pe_date, "priceDate": price_date}


def load() -> dict[str, Any]:
    """全部股票的河流位置（10 分鐘快取；本益比或日K換日就重算）。"""
    initialize_database()
    with get_connection() as connection:
        try:
            pe_key = connection.execute("SELECT MAX(trade_date) AS d, COUNT(*) AS n FROM stock_pe_daily").fetchone()
            key = (pe_key["d"], pe_key["n"])
        except Exception:  # noqa: BLE001
            key = None
    with _lock:
        if _cache["data"] is not None and _cache["key"] == key and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["data"]
    data = _build()
    with _lock:
        _cache.update({"key": key, "at": time.time(), "data": data})
    return data


def zones() -> dict[str, int]:
    """{代號: 河流區序號 0～5}（選股系統用；沒河道的不列）。"""
    return {code: r["zone"] for code, r in load()["rows"].items() if not r["noVal"]}


def _resolve(text: str, rows: dict[str, dict[str, Any]]) -> str | None:
    q = str(text or "").strip().upper()
    if not q:
        return None
    if q in rows:
        return q
    exact = [c for c, r in rows.items() if r["name"].upper() == q]
    if exact:
        return exact[0]
    part = sorted(c for c, r in rows.items() if q in r["name"].upper())
    return part[0] if part else None


def _history(code: str, ref: float, days: int = CHART_DAYS) -> dict[str, Any]:
    """近 days 根日K收盤，加上每天的河道（當時的近 4 季 EPS × 現在的族群本益比；沒本益比的日子沿用前一個）。"""
    from fundamentals_daily import pe_history

    with get_connection() as connection:
        rows = connection.execute(
            "SELECT substr(bar_time, 1, 10) AS d, close FROM bars_1d WHERE stock_code = ? ORDER BY bar_time DESC LIMIT ?", (code, days)
        ).fetchall()
    bars = sorted((str(r["d"]), float(r["close"])) for r in rows)
    closes = dict(bars)
    eps_points = []
    for p in pe_history(code):
        value = _valid_pe(p["pe"])
        close = closes.get(p["date"])
        if value and close:
            eps_points.append((p["date"], close / value))
    dates, close_list, bands = [], [], [[] for _ in RATIOS]
    quarters: list[dict[str, Any]] = []
    j, eps = 0, None
    for d, c in bars:
        while j < len(eps_points) and eps_points[j][0] <= d:
            new = eps_points[j][1]
            if eps is None or abs(new / eps - 1) > 0.01:      # 近 4 季 EPS 變了（新一季財報）
                quarters.append({"start": d, "eps4": round(new, 2)})
            eps = new
            j += 1
        dates.append(d)
        close_list.append(c)
        for k, ratio in enumerate(RATIOS):
            bands[k].append(round(eps * ref * ratio, 2) if eps else None)
    return {"dates": dates, "close": close_list, "bands": bands, "steps": quarters}


def _revenue(code: str) -> dict[str, Any]:
    from fundamentals_daily import _schema as _fund_schema

    with get_connection() as connection:
        _fund_schema(connection)
        rows = connection.execute("SELECT ym, revenue, yoy_pct FROM stock_revenue_monthly WHERE stock_code = ? ORDER BY ym DESC LIMIT 15", (code,)).fetchall()
    recent = [{"ym": str(r["ym"]), "rev": round((r["revenue"] or 0) / 100000, 1), "yoy": r["yoy_pct"]} for r in rows[:3]]
    ytd = None
    if rows:
        year = str(rows[0]["ym"])[:4]
        this = [r for r in rows if str(r["ym"]).startswith(year)]
        # 今年累計年增：用每月營收與年增率推回去年同期
        cur = sum(r["revenue"] or 0 for r in this)
        prev = sum((r["revenue"] or 0) / (1 + (r["yoy_pct"] or 0) / 100) for r in this if r["yoy_pct"] is not None and r["yoy_pct"] > -100)
        ytd = round((cur / prev - 1) * 100, 1) if prev else None
    return {"rev": recent, "ytd": ytd}


def query(text: str) -> dict[str, Any]:
    data = load()
    code = _resolve(text, data["rows"])
    if not code:
        raise LookupError(f"找不到「{text}」（要有本益比資料的上市櫃股票）")
    row = dict(data["rows"][code])
    row["fund"] = _revenue(code)
    if not row["noVal"]:
        row["chart"] = _history(code, row["ref"])
    row["groupPe"] = {"group": row["refGroup"], "pe": row["ref"]}
    return {"status": "ok", "stock": row, "peDate": data["peDate"], "priceDate": data["priceDate"]}


def ma10(min_score: int = MA10_MIN_SCORE) -> dict[str, Any]:
    """⭐ 均線分數 ≥10 × 便宜區（含跌破特價、特價、便宜）：族群成員，依均線分數高到低、再依離分水嶺遠到近。"""
    data = load()
    groups = _groups_of()
    rows = [r for c, r in data["rows"].items() if c in groups and not r["noVal"] and r["zone"] <= 2 and (r["score"] or 0) >= min_score]
    rows.sort(key=lambda r: (-(r["score"] or 0), r["dist"], r["code"]))
    keep = ("code", "name", "group", "score", "price", "zone", "zoneName", "dist", "s", "ref", "refGroup")
    return {"status": "ok", "date": data["priceDate"], "count": len(rows), "rows": [{k: r.get(k) for k in keep} for r in rows]}


def stock_list() -> list[dict[str, str]]:
    return [{"code": c, "name": r["name"]} for c, r in sorted(load()["rows"].items())]
