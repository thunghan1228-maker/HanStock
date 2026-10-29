"""每季財報（2026-10-16 使用者：估值河流圖的單季 EPS、營益率、近 4 季 EPS 要跟對方一樣用財報）。

資料：公開資訊觀測站「綜合損益表 彙總報表」（上市＋上櫃）每季一份。觀測站擋正式站主機，由排程主機（tw-groups 的
mops-financials 排程）抓到 data 分支 tpex/fin-YYYYQn.json，清單在 tpex/fin-index.json；這裡定時看清單、排程抓完也會直接戳。
彙總表的數字是「今年累計到該季」：營業收入、營業利益（千元）、基本每股盈餘（元）。
單季＝這季累計 − 同年上一季累計（第 1 季就是累計本身）；第 4 季＝全年 − 前三季累計。
近 4 季 EPS＝最近 4 個連續單季相加（交易所的本益比要等它自己換季，財報一公布這裡就跟上）。
營益率＝單季營業利益 ÷ 單季營業收入（金融業沒有營業收入／營業利益就不算）。

2026-10-29 使用者：老師頁有「Q3 季報 EPS 族群分析、多觀察分析」——彙總表再多存營業毛利、稅後淨利（單季毛利率、淨利率、三率三升、毛利率比上季多／少
2 個百分點的 🚀／🐢），每家第一次被抓到的日期（first_seen／known：公布隔日漲跌用；公布期間陸續出來的季，第一次存檔那批 baseline 日期不確定），
eps_board() 把一季的公布狀況整成一張表給前端畫族群卡、個股卡。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from chips_daily import _default_fetcher, _mirror_url
from database import get_connection, initialize_database

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))
MIRROR_INDEX = "fin-index.json"
KEEP_SEASONS = 12
POLL_SECONDS = 30 * 60
FIELDS = ("name", "market", "revenue", "opIncome", "eps")
GM_MOVE_PP = 2.0            # 🚀／🐢：單季毛利率比上一季多／少 2 個百分點以上
TURN_YOY = 30.0             # 單季 EPS 年增 ≥30% 或轉盈：橘；≥100% 紅

_state: dict[str, Any] = {"lastCollect": None, "lastError": None, "result": None, "indexUpdated": None}
_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "singles": None}
_thread: Optional[threading.Thread] = None


def _schema(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS fin_season_cum (
            code TEXT NOT NULL, season TEXT NOT NULL, market TEXT, name TEXT,
            revenue REAL, op_income REAL, eps REAL, updated_at TEXT, PRIMARY KEY (code, season)
        );
        CREATE INDEX IF NOT EXISTS fin_season_cum_season_idx ON fin_season_cum (season);
        """
    )
    have = {str(r["name"]) for r in connection.execute("PRAGMA table_info(fin_season_cum)").fetchall()}
    for column, kind in (("gross", "REAL"), ("net", "REAL"), ("first_seen", "TEXT"), ("known", "INTEGER")):
        if column not in have:
            connection.execute(f"ALTER TABLE fin_season_cum ADD COLUMN {column} {kind}")


def parse_season(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """鏡像 {season:'2026Q2', fields:[name,market,revenue,opIncome,eps(,gross,net)], rows:{代號:[...]}, seen:{代號:第一次抓到的日期}, baseline:日期}。
    seen／baseline 都有、而且這家不是第一次存檔那批（seen ≠ baseline）→ known（公布日確定）。"""
    if not isinstance(payload, dict):
        return None, []
    season = str(payload.get("season") or "")
    if len(season) != 6 or season[4] != "Q" or not season[:4].isdigit() or season[5] not in "1234":
        return None, []
    fields = payload.get("fields") or list(FIELDS)
    out = []
    for code, row in (payload.get("rows") or {}).items():
        if not isinstance(row, list) or len(row) < len(fields):
            continue
        item = dict(zip(fields, row))
        code = str(code).strip().upper()
        seen = (payload.get("seen") or {}).get(code) or (payload.get("seen") or {}).get(str(code))
        baseline = payload.get("baseline")
        out.append({"code": code, **item, "firstSeen": seen, "known": bool(seen and baseline and seen != baseline)})
    return season, out


def save_season(season: str, rows: list[dict[str, Any]], stamp: str | None = None) -> int:
    if not rows:
        return 0
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            """INSERT INTO fin_season_cum (code, season, market, name, revenue, op_income, eps, updated_at, gross, net, first_seen, known)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(code, season) DO UPDATE SET market = excluded.market, name = excluded.name, revenue = excluded.revenue,
                   op_income = excluded.op_income, eps = excluded.eps, updated_at = excluded.updated_at,
                   gross = COALESCE(excluded.gross, fin_season_cum.gross), net = COALESCE(excluded.net, fin_season_cum.net),
                   first_seen = COALESCE(fin_season_cum.first_seen, excluded.first_seen),
                   known = CASE WHEN fin_season_cum.first_seen IS NULL THEN excluded.known ELSE fin_season_cum.known END""",
            [(r["code"], season, r.get("market"), r.get("name"), r.get("revenue"), r.get("opIncome"), r.get("eps"), stamp,
              r.get("gross"), r.get("net"), r.get("firstSeen"), 1 if r.get("known") else 0) for r in rows],
        )
    with _lock:
        _cache["key"] = None
    return len(rows)


def collect(now: datetime | None = None, fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    now = now or datetime.now(TW_TZ)
    call = fetcher or _default_fetcher
    result: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "seasons": {}, "errors": []}
    index = call(_mirror_url(MIRROR_INDEX, volatile=True))
    seasons = [str(s) for s in (index.get("seasons") if isinstance(index, dict) else None) or []][:KEEP_SEASONS]
    for season in seasons:
        try:
            got, rows = parse_season(call(_mirror_url(f"fin-{season}.json", volatile=True)))
            if got != season:
                raise RuntimeError(f"鏡像季別 {got} 不是 {season}")
            result["seasons"][season] = save_season(season, rows, result["at"])
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{season}: {type(exc).__name__}: {exc}")
    with _lock:
        _state["lastCollect"] = result["at"]
        _state["lastError"] = "; ".join(result["errors"]) or None
        _state["result"] = {k: v for k, v in result.items() if k != "errors"}
        _state["indexUpdated"] = index.get("updated") if isinstance(index, dict) else None
    return result


# ------------------------------------------------------------------ 計算

def _prev(season: str) -> str:
    y, q = int(season[:4]), int(season[5])
    return f"{y - 1}Q4" if q == 1 else f"{y}Q{q - 1}"


def label(season: str) -> str:
    """'2026Q2' → '26Q2'。"""
    return season[2:]


def _minus(a: Any, b: Any) -> float | None:
    return None if a is None or b is None else a - b


def _singles_from(cum: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """一檔各季累計 → 單季（新到舊）。缺上一季累計的季（第 1 季除外）算不出來就跳過。"""
    out = []
    for season in sorted(cum, reverse=True):
        c = cum[season]
        if season.endswith("Q1"):
            eps, rev, opi, gro, net = c["eps"], c["revenue"], c["op_income"], c.get("gross"), c.get("net")
        else:
            p = cum.get(_prev(season))
            if p is None:
                continue
            eps, rev, opi = _minus(c["eps"], p["eps"]), _minus(c["revenue"], p["revenue"]), _minus(c["op_income"], p["op_income"])
            gro, net = _minus(c.get("gross"), p.get("gross")), _minus(c.get("net"), p.get("net"))
        margin = round(opi / rev * 100, 1) if rev and opi is not None and rev > 0 else None
        out.append({"season": season, "label": label(season), "eps": round(eps, 2) if eps is not None else None,
                    "revenue": rev, "opIncome": opi, "margin": margin,
                    "gm": round(gro / rev * 100, 1) if rev and gro is not None and rev > 0 else None,
                    "nm": round(net / rev * 100, 1) if rev and net is not None and rev > 0 else None,
                    "raw": {"gm": gro / rev * 100 if rev and gro is not None and rev > 0 else None,
                            "om": opi / rev * 100 if rev and opi is not None and rev > 0 else None,
                            "nm": net / rev * 100 if rev and net is not None and rev > 0 else None},
                    "firstSeen": c.get("first_seen"), "known": bool(c.get("known"))})
    return out


def all_singles() -> dict[str, list[dict[str, Any]]]:
    """{代號: 單季（新到舊）}（財報有更新才重算）。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        key_row = connection.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS u FROM fin_season_cum").fetchone()
        key = (key_row["n"], key_row["u"])
        with _lock:
            if _cache["key"] == key and _cache["singles"] is not None:
                return _cache["singles"]
        rows = connection.execute("SELECT code, season, revenue, op_income, eps, gross, net, first_seen, known FROM fin_season_cum").fetchall()
    by_code: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by_code.setdefault(str(r["code"]), {})[str(r["season"])] = {
            "revenue": r["revenue"], "op_income": r["op_income"], "eps": r["eps"], "gross": r["gross"], "net": r["net"],
            "first_seen": r["first_seen"], "known": r["known"]}
    singles = {code: _singles_from(cum) for code, cum in by_code.items()}
    with _lock:
        _cache.update({"key": key, "singles": singles})
    return singles


def eps4_of(singles: list[dict[str, Any]], asof: str | None = None) -> dict[str, Any] | None:
    """最近 4 個連續單季（asof＝只看這一季以前的）的 EPS 合計；不連續或缺 EPS 就 None。"""
    rows = [s for s in singles if asof is None or s["season"] <= asof]
    if len(rows) < 4:
        return None
    four = rows[:4]
    for a, b in zip(four, four[1:]):
        if _prev(a["season"]) != b["season"]:
            return None
    if any(s["eps"] is None for s in four):
        return None
    return {"eps4": round(sum(s["eps"] for s in four), 2), "latest": four[0]["season"], "label": label(four[0]["season"])}


def eps4_map() -> dict[str, dict[str, Any]]:
    out = {}
    for code, singles in all_singles().items():
        got = eps4_of(singles)
        if got:
            out[code] = got
    return out


# 季報公布期限（這天以後股價就反映得到那一季）：Q1 5/15、Q2 8/14、Q3 11/14、Q4 隔年 3/31
DEADLINE = {1: "05-15", 2: "08-14", 3: "11-14", 4: "03-31"}


def published_on(season: str) -> str:
    y, q = int(season[:4]), int(season[5])
    return f"{y + 1 if q == 4 else y}-{DEADLINE[q]}"


def eps4_steps(singles: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """[(從哪天起, 近 4 季 EPS)]（舊到新）：每一季的公布期限一到，近 4 季 EPS 換成含那一季的合計。"""
    steps = []
    for s in sorted(singles, key=lambda x: x["season"]):
        got = eps4_of(singles, s["season"])
        if got:
            steps.append((published_on(s["season"]), got["eps4"]))
    return steps


# ------------------------------------------------------------------ 季報 EPS 榜（2026-10-29 使用者：老師頁的 Q3 季報 EPS 族群分析、多觀察分析）

EPS_BOARD_FIELDS = ["code", "name", "market", "eps", "last", "yoy", "gm", "gmPrev", "gmDelta", "om", "omPrev", "nm", "nmPrev",
                    "triple", "rocket", "turtle", "tier", "announce", "known", "next", "close"]
MARKET_LABEL = {"sii": "上市", "otc": "上櫃", "TSE": "上市", "OTC": "上櫃"}


def _last_year(season: str) -> str:
    return f"{int(season[:4]) - 1}{season[4:]}"


def eps_tier(eps: float | None, last: float | None) -> str:
    """顏色：t100＝單季 EPS 年增 ≥100%；t30＝30～100% 或去年虧今年賺（轉盈）；t0＝0～30%；tneg＝年減或本季虧損；tnone＝去年虧本季也沒賺、或沒有去年同季。"""
    if eps is None:
        return "tnone"
    if eps < 0:
        return "tneg"
    if last is None:
        return "tnone"
    if last <= 0:
        return "t30" if eps > 0 else "tnone"
    yoy = (eps / last - 1) * 100
    return "t100" if yoy >= 100 else "t30" if yoy >= TURN_YOY else "t0" if yoy >= 0 else "tneg"


def _moved(now: float | None, prev: float | None) -> float | None:
    """百分點差（用沒四捨五入的利潤率算，才跟老師頁的 ▲7.2pp、▼0.3pp 一樣）。"""
    return None if now is None or prev is None else round(now - prev, 1)


def eps_board(season: str | None = None) -> dict[str, Any]:
    """一季的季報公布狀況：每家已公布的單季 EPS、去年同季、年增、單季毛利率／營益率／淨利率與上一季的差、三率三升、🚀／🐢（毛利率比上季多／少 2 個百分點）、
    公布日與公布隔日漲跌（日期是我們第一次抓到的日子；第一次存檔那批 known＝False 不算）。族群、多觀察名單由前端用族群表分。"""
    from revenue_rank import _closes, next_day_change

    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        seasons = [str(r["season"]) for r in connection.execute(
            "SELECT season, COUNT(*) AS n FROM fin_season_cum WHERE eps IS NOT NULL GROUP BY season HAVING n > 0 ORDER BY season DESC").fetchall()]
    if not seasons:
        raise LookupError("還沒有財報資料")
    season = season or seasons[0]
    if season not in seasons:
        raise LookupError(f"{season} 沒有財報資料")
    singles = all_singles()
    prev_season, last_season = _prev(season), _last_year(season)
    rows: list[dict[str, Any]] = []
    for code, lst in singles.items():
        by_season = {s["season"]: s for s in lst}
        cur = by_season.get(season)
        if not cur or cur["eps"] is None:
            continue
        prev, last = by_season.get(prev_season), by_season.get(last_season)
        last_eps = last["eps"] if last else None
        yoy = round((cur["eps"] / last_eps - 1) * 100, 1) if last_eps is not None and last_eps > 0 else None
        pr = prev["raw"] if prev else {}
        gm_delta = _moved(cur["raw"]["gm"], pr.get("gm"))
        om_delta = _moved(cur["raw"]["om"], pr.get("om"))
        nm_delta = _moved(cur["raw"]["nm"], pr.get("nm"))
        triple = bool(gm_delta is not None and om_delta is not None and nm_delta is not None and gm_delta > 0 and om_delta > 0 and nm_delta > 0)
        rows.append({"code": code, "eps": cur["eps"], "last": last_eps, "yoy": yoy, "gm": cur["gm"], "gmPrev": prev["gm"] if prev else None, "gmDelta": gm_delta,
                     "om": cur["margin"], "omPrev": prev["margin"] if prev else None, "nm": cur["nm"], "nmPrev": prev["nm"] if prev else None,
                     "triple": triple, "rocket": bool(gm_delta is not None and gm_delta >= GM_MOVE_PP), "turtle": bool(gm_delta is not None and gm_delta <= -GM_MOVE_PP),
                     "tier": eps_tier(cur["eps"], last_eps), "announce": cur.get("firstSeen"), "known": bool(cur.get("known"))})
    with get_connection() as connection:
        meta = {str(r["code"]): (r["name"], r["market"]) for r in connection.execute(
            "SELECT code, name, market FROM fin_season_cum WHERE season = ?", (season,)).fetchall()}
    known_days = sorted({r["announce"] for r in rows if r["known"] and r["announce"]})
    since = (datetime.strptime(known_days[0], "%Y-%m-%d") - timedelta(days=12)).strftime("%Y-%m-%d") if known_days else None
    series = _closes(sorted({r["code"] for r in rows}), since) if since else {}
    out = []
    for r in rows:
        s = series.get(r["code"], [])
        r["next"] = next_day_change(s, r["announce"]) if r["known"] and r["announce"] and s else None
        r["close"] = s[-1][1] if s else None
        name, market = meta.get(r["code"], ("", ""))
        r["name"], r["market"] = name, MARKET_LABEL.get(market, market)
        out.append([r[k] for k in EPS_BOARD_FIELDS])
    out.sort(key=lambda x: (x[5] is None, -(x[5] or 0), x[0]))
    price_date = max((v[-1][0] for v in series.values() if v), default=None)
    return {"status": "ok", "season": season, "label": f"{season[:4]} Q{season[5]}", "seasons": seasons, "prevSeason": prev_season, "lastYearSeason": last_season,
            "fields": EPS_BOARD_FIELDS, "rows": out, "published": len(out), "announceDays": known_days, "latestDay": known_days[-1] if known_days else None,
            "priceDate": price_date}


def collector_status() -> dict[str, Any]:
    with _lock:
        return {k: _state[k] for k in ("lastCollect", "lastError", "result", "indexUpdated")}


def run_collect() -> dict[str, Any]:
    try:
        return collect()
    except Exception as exc:  # noqa: BLE001
        logger.exception("每季財報抓資料失敗")
        with _lock:
            _state["lastError"] = f"{type(exc).__name__}: {exc}"
        return {"error": str(exc)}


def _loop() -> None:
    run_collect()
    while True:
        time.sleep(POLL_SECONDS)
        try:   # 鏡像清單有更新才整份重拉
            index = _default_fetcher(_mirror_url(MIRROR_INDEX, volatile=True))
            if isinstance(index, dict) and index.get("updated") != _state.get("indexUpdated"):
                run_collect()
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _state["lastError"] = f"index: {type(exc).__name__}: {exc}"


def start_financials_collector() -> bool:
    global _thread
    if _thread and _thread.is_alive():
        return False
    _thread = threading.Thread(target=_loop, name="financials", daemon=True)
    _thread.start()
    return True
