"""營收＋技術面綜合評分（2026-11-22 使用者：營收面跟技術面兩個綜合評分比較好的個股，抓 20 檔，放首頁第二欄，每天更新）。

綜合分（滿分 100）＝營收分（滿分 50）＋技術分（滿分 50）：
- 營收分：每家公司最新一個已公布月份（每月 1～10 號陸續公布，還沒公布當月的用上個月）
  - 單月年增：≥100% 20 分、≥50% 16、≥30% 12、≥15% 8、≥5% 4、>0 2
  - 累計年增：≥50% 15 分、≥30% 12、≥15% 8、≥5% 4、>0 2
  - 年增加速（這個月年增比上個月年增）：年增 >0 才算，多 10 個百分點以上 10 分、持平或變好 6、少不到 10 個百分點 3
  - 月增 >0：5 分
- 技術分：全站同一套均線分數（黑龍表 heilong_daily 的 score2：站上 6 條均線＋創 6 個天期新高＋多頭排列 3，滿分 15）× 50 ÷ 15
只看 5 日均成交值 ≥ 5,000 萬的（太冷門的不列）。技術面每天收盤後黑龍表整理完就換（上市約 15:00、上櫃鏡像約 17:00），營收抓到新公布就換。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_connection, initialize_database

TW_TZ = timezone(timedelta(hours=8))
TOP = 20
MAX_TOP = 50
MIN_VAL5 = 0.5               # 5 日均成交值（億）
CACHE_SECONDS = 600
YOY_STEPS = ((100, 20), (50, 16), (30, 12), (15, 8), (5, 4), (0, 2))
CUM_STEPS = ((50, 15), (30, 12), (15, 8), (5, 4), (0, 2))

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "rows": None, "meta": None}


def _steps(value: float | None, steps: tuple[tuple[float, int], ...]) -> int:
    if value is None:
        return 0
    for floor, points in steps:
        if value > floor if floor == 0 else value >= floor:
            return points
    return 0


def revenue_points(yoy: float | None, cum_yoy: float | None, mom: float | None, prev_yoy: float | None) -> int:
    points = _steps(yoy, YOY_STEPS) + _steps(cum_yoy, CUM_STEPS)
    if yoy is not None and yoy > 0 and prev_yoy is not None:
        diff = yoy - prev_yoy
        points += 10 if diff >= 10 else 6 if diff >= 0 else 3 if diff > -10 else 0
    if mom is not None and mom > 0:
        points += 5
    return points


def tech_points(score2: int | None) -> int:
    return 0 if score2 is None else int(round(score2 * 50 / 15))


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _latest_revenue() -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """{代號: 最新已公布月份那列＋上個月年增}、{月份: 用到幾家}；只看最新兩個月（太舊的不算）。"""
    from revenue_rank import _schema as revenue_schema

    with get_connection() as connection:
        revenue_schema(connection)
        months = [r["ym"] for r in connection.execute("SELECT DISTINCT ym FROM revenue_month ORDER BY ym DESC LIMIT 2").fetchall()]
        if not months:
            return {}, {}
        wanted = sorted(set(months) | {_prev_month(m) for m in months})
        rows = connection.execute(
            f"SELECT code, ym, market, name, industry, rev, mom, yoy, cum_yoy FROM revenue_month WHERE ym IN ({','.join('?' for _ in wanted)})",
            tuple(wanted),
        ).fetchall()
    by_month: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_month.setdefault(str(row["ym"]), {})[str(row["code"]).strip().upper()] = dict(row)
    latest: dict[str, dict[str, Any]] = {}
    used: dict[str, int] = {}
    for month in months:                      # 新的月份先
        for code, row in by_month.get(month, {}).items():
            if code in latest:
                continue
            prev = by_month.get(_prev_month(month), {}).get(code)
            latest[code] = {**row, "prev_yoy": prev["yoy"] if prev else None}
            used[month] = used.get(month, 0) + 1
    return latest, used


def _build() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from brew_launch_history import group_and_name   # 延遲匯入：啟動時會連到很多模組
    from heilong_backtest import _schema as heilong_schema

    initialize_database()
    with get_connection() as connection:
        heilong_schema(connection)
        day = connection.execute("SELECT MAX(trade_date) AS d FROM heilong_daily WHERE score2 IS NOT NULL").fetchone()["d"]
        tape = connection.execute(
            "SELECT stock_code, close, change_pct, score2, val5, hi_len FROM heilong_daily WHERE trade_date = ? AND score2 IS NOT NULL", (day,)
        ).fetchall() if day else []
    revenue, used = _latest_revenue()
    rows = []
    for t in tape:
        code = str(t["stock_code"]).strip().upper()
        r = revenue.get(code)
        if not r or t["val5"] is None or float(t["val5"]) < MIN_VAL5:
            continue
        rev, tech = revenue_points(r["yoy"], r["cum_yoy"], r["mom"], r["prev_yoy"]), tech_points(t["score2"])
        group, name = group_and_name(code)
        rows.append({
            "code": code, "name": r["name"] if name == code else name, "group": group or (r["industry"] or ""),
            "market": r["market"], "total": rev + tech, "rev": rev, "tech": tech, "score2": int(t["score2"]),
            "yoy": r["yoy"], "cumYoy": r["cum_yoy"], "mom": r["mom"], "prevYoy": r["prev_yoy"], "revMonth": r["ym"],
            "revYi": round(r["rev"] / 100_000, 2) if r["rev"] is not None else None,
            "close": t["close"], "chg": t["change_pct"], "val5": t["val5"], "hiLen": t["hi_len"],
        })
    rows.sort(key=lambda x: (-x["total"], -x["tech"], -(x["yoy"] if x["yoy"] is not None else -1e9), x["code"]))
    return rows, {"date": day, "revMonths": dict(sorted(used.items(), reverse=True)), "candidates": len(rows)}


def payload(top: int = TOP) -> dict[str, Any]:
    top = max(1, min(MAX_TOP, int(top)))
    with _lock:
        if _cache["rows"] is None or time.monotonic() - _cache["at"] >= CACHE_SECONDS:
            rows, meta = _build()
            _cache.update({"rows": rows, "meta": meta, "at": time.monotonic()})
        rows, meta = _cache["rows"], _cache["meta"]
    if not rows:
        return {"status": "empty", "message": "還沒有營收或技術面資料", **meta, "rows": []}
    return {"status": "ok", **meta, "top": top, "rows": rows[:top],
            "rule": {"minVal5Yi": MIN_VAL5, "revMax": 50, "techMax": 50},
            "generatedAt": datetime.now(TW_TZ).isoformat(timespec="seconds")}
