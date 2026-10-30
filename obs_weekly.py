"""收盤觀察股 週分析（2026-10-29 使用者：波段日報新增「每週多觀察全方面解析」，照老師「收盤觀察股 週分析」頁做）。

名單＝形態觀察股（首頁「多觀察」那欄、前端「強勢黑K選股」同一套規則）的週版：
每天的觀察池＝近 10 個交易日（含當天）被「創高黑龍」（均線分數 ≥10、收黑、漲跌 −10%～+3%）選到至少 2 次，而且當天均線分數 ≥15；
「名單」＝近 20 個交易日（約四週）內任何一天進過觀察池的股票——進池的當天均線分一定是 15 分、站上月線，後來轉弱掉到月線下的還留在名單裡，
所以才看得到「月線下」「危險個股」，超過 20 個交易日沒再進池的才刪除。可以回推任何一個交易日。
老師的名單是他自己的系統選的、我們沒有，所以檔數、名字跟老師那份不會一樣。

九個段落：①名單異動（對上一週最後一份名單）②族群總覽 ③功課重點（三者同時成立）④已噴在高檔（▲）⑤月線下（含「即將穿越」）
⑥危險個股（健診 基本／籌碼／技術 三科都低於 60）⑦觀察（其餘）⑧四週回測（過去四週每週最後一份名單，名單日收盤 → 這份的收盤）
⑨給同學的功課提醒（規則產生的文字）。資料都是收盤後的，沒有即時報價。
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from database import get_connection, initialize_database
from heilong_backtest import _load_bars, _schema as _heilong_schema
from ma_rank import HIDE_GROUPS, _stock_name, _universe
from obs_teacher_lists import TEACHER_LISTS, seed_at

logger = logging.getLogger("hanstock.obs_weekly")

POOL_DAYS = 10                 # 觀察池：近 10 個交易日（含當天）
POOL_MIN_HITS = 2              # 創高黑龍至少選到 2 次
POOL_MIN_SCORE = 15            # 當天均線分數 ≥15
LIST_DAYS = 20                 # 名單：近 20 個交易日（約四週）內進過觀察池的；沒再進池超過這麼久才刪除
DRAGON_MIN_SCORE = 10          # 創高黑龍：均線分數 ≥10、收黑、漲跌 −10%～+3%（跟選股頁 score=10&k=black&kmin=-10&kmax=3 一樣）
DRAGON_CHG_MIN, DRAGON_CHG_MAX = -10.0, 3.0
HIGH_DIST = 15.0               # ▲高檔：離月線 ≥15% 或 20 日漲 ≥30%（老師頁的圖例）
HIGH_RET20 = 30.0
LIT_PCT = 9.5                  # ●今日漲停（漲幅 ≥9.5%；老師頁 GIS-KY +9.9%、麗臺 +9.7% 都有●，華新科 +6.8%、陽程 +4.6% 沒有）
CROSS_MAX = 9.0                # 即將穿越：月線下、距月線 ≤9%（老師頁 6.6%／1.9%／8.4% 有標，9.8%／11.1% 沒標）
DANGER_BELOW = 60              # 危險個股：健診 基本／籌碼／技術 三科都低於 60 分
HOMEWORK_MIN_SCORE = 6         # 三者同時成立：均線分 ≥6、籌碼不為負、基本面有撐
HOMEWORK_FUND_MIN = 50         # 基本面有撐：健診基本面 ≥50 而且最新一季 EPS 不是虧損
WEEKS_BACK = 4                 # 四週回測看過去幾週
SERIES_DAYS = 5                # 均線分走勢看幾天
SMALL_GROUP = 2                # 族群總覽：不到這個檔數的併進「其他」
CHIP_ABNORMAL = 20.0           # 集保週變化超過 ±20% 當數值異常（可能是股本結構變動）
MA_FALLBACK_DAYS = 25          # 月線／20 日漲的備援：黑龍表連續往前讀幾天
MA_BAR_DAYS = 160              # 讀日K往前幾個日曆天（月線、20 日漲、三K低夠用）
CACHE_SECONDS = 300

_lock = threading.Lock()
_cache: dict[str, Any] = {}


# ------------------------------------------------------------------ 資料

def _dates() -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _heilong_schema(connection)
        return [str(r["trade_date"]) for r in connection.execute(
            "SELECT DISTINCT trade_date FROM heilong_daily WHERE score2 IS NOT NULL ORDER BY trade_date").fetchall()]


def _load_days(days: list[str], codes: list[str]) -> dict[str, dict[str, tuple]]:
    """{日期: {代號: (開, 高, 低, 收, 漲跌%, 均線分)}}，只取族群成員。"""
    out: dict[str, dict[str, tuple]] = {d: {} for d in days}
    if not days or not codes:
        return out
    want = set(codes)
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT trade_date, stock_code, open, high, low, close, change_pct, score2 FROM heilong_daily "
            f"WHERE trade_date IN ({','.join('?' for _ in days)})", tuple(days)).fetchall()      # 新上市櫃還算不出均線分數的（漢達）分數是空的，也要讀進來（老師名單裡有）
    for r in rows:
        code = str(r["stock_code"]).strip().upper()
        if code in want:
            out[str(r["trade_date"])][code] = (
                float(r["open"] or 0), float(r["high"] or 0), float(r["low"] or 0), float(r["close"] or 0),
                None if r["change_pct"] is None else float(r["change_pct"]), int(r["score2"] or 0))
    return out


def _is_dragon(rec: tuple) -> bool:
    o, _h, _l, c, chg, score = rec
    return score >= DRAGON_MIN_SCORE and c < o and chg is not None and DRAGON_CHG_MIN <= chg <= DRAGON_CHG_MAX


def hits_at(day: str, dates: list[str], rows_by_day: dict[str, dict[str, tuple]]) -> dict[str, int]:
    """{代號: 近 10 天（含 day）創高黑龍次數}（只列有被選到過的）。"""
    i = dates.index(day)
    hits: dict[str, int] = {}
    for d in dates[max(0, i - POOL_DAYS + 1):i + 1]:
        for code, rec in rows_by_day.get(d, {}).items():
            if _is_dragon(rec):
                hits[code] = hits.get(code, 0) + 1
    return hits


def pool_at(day: str, dates: list[str], rows_by_day: dict[str, dict[str, tuple]]) -> dict[str, int]:
    """{代號: 創高黑龍次數}：day 當天的收盤版觀察池（至少 2 次、當天均線分數 ≥15）。"""
    today = rows_by_day.get(day, {})
    return {c: n for c, n in hits_at(day, dates, rows_by_day).items() if n >= POOL_MIN_HITS and c in today and today[c][5] >= POOL_MIN_SCORE}


def list_at(day: str, dates: list[str], rows_by_day: dict[str, dict[str, tuple]]) -> dict[str, dict[str, Any]]:
    """day 當天的名單：{代號: {last（最近一次進池的日期）, days（近 20 個交易日進池幾天）}}。
    有老師公布的名單（obs_teacher_lists）就以它為基準：名單日當天就是老師那份，之後再加上這之後新進觀察池的股票；沒有就照規則算。"""
    i = dates.index(day)
    out: dict[str, dict[str, Any]] = {}
    seed = seed_at(day)
    if seed:
        seed_day, info = seed
        for code in info["codes"]:
            out[code] = {"last": seed_day, "days": 1}
        for d in dates:
            if seed_day < d <= day:
                for code in pool_at(d, dates, rows_by_day):
                    cur = out.setdefault(code, {"last": d, "days": 0})
                    cur["last"] = d
                    cur["days"] += 1
        return out
    for d in dates[max(0, i - LIST_DAYS + 1):i + 1]:
        for code in pool_at(d, dates, rows_by_day):
            info = out.setdefault(code, {"last": d, "days": 0})
            info["last"] = d
            info["days"] += 1
    return out


def _weekly_list_dates(dates: list[str], day: str, weeks: int = WEEKS_BACK) -> list[str]:
    """過去 weeks 週、每週最後一個交易日（新到舊）；不含 day 這一週。"""
    def week_key(d: str) -> tuple[int, int]:
        iso = datetime.strptime(d, "%Y-%m-%d").isocalendar()
        return iso[0], iso[1]

    this_week = week_key(day)
    last_of: dict[tuple[int, int], str] = {}
    for d in dates:
        if d < day and week_key(d) < this_week:
            last_of[week_key(d)] = d
    return [last_of[k] for k in sorted(last_of, reverse=True)[:weeks]]


# ------------------------------------------------------------------ 指標

def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round((a / b - 1) * 100, 1)


def _median(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 1) if values else None


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _primary_group(code: str, groups_of: dict[str, list[str]]) -> str:
    mine = [g for g in groups_of.get(code, []) if g not in HIDE_GROUPS]
    return mine[0] if mine else "其他"


def _closes(code: str, bars: dict[str, list], rows_by_day: dict[str, dict[str, tuple]], dates: list[str], day: str) -> list[tuple[str, float]]:
    """(日期, 收盤) 舊到新、到 day 為止：優先用還原日K，沒有就用黑龍表那幾天的收盤。"""
    series = bars.get(code)
    if series:
        return [(b[0], b[4]) for b in series if b[0] <= day]
    return [(d, rows_by_day[d][code][3]) for d in dates if d <= day and code in rows_by_day.get(d, {})]


def _stock_item(code: str, day: str, ctx: dict[str, Any]) -> dict[str, Any]:
    rows_by_day, dates, bars = ctx["rows"], ctx["dates"], ctx["bars"]
    rec = rows_by_day[day][code]
    if rec[5] is None:    # 新上市櫃還不到 240 天、算不出均線分數
        rec = (*rec[:5], 0)
    series = _closes(code, bars, rows_by_day, dates, day)
    closes = [c for _d, c in series]
    ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None
    close = rec[3]
    dist20 = _pct(close, ma20)
    ret20 = _pct(closes[-1], closes[-21]) if len(closes) >= 21 else None
    i = dates.index(day)
    score_series = [(rows_by_day[d][code][5] or 0) for d in dates[max(0, i - SERIES_DAYS + 1):i + 1] if code in rows_by_day.get(d, {})]
    lows = [b[3] for b in (bars.get(code) or []) if b[0] < day][-3:]
    brk3 = bool(len(lows) == 3 and close < min(lows))
    inst = ctx["inst"].get(code) or {}
    ck = ctx["checkup"].get(code) or {}
    scores = ck.get("scores") or {}
    chip = ctx["tdcc"].get(code) or {}
    rev = ctx["revenue"].get(code) or {}
    single = (ctx["singles"].get(code) or [None])[0]
    chg = rec[4]
    item = {
        "code": code, "name": ctx["names"].get(code, code), "grp": _primary_group(code, ctx["groupsOf"]),
        "close": close, "chg": chg, "score": rec[5], "scoreSeries": score_series,
        "ma20": ma20, "dist20": dist20, "ret20": ret20,
        "inst5": inst.get("inst5"), "inst10": inst.get("inst10"),
        "chipWeek": chip.get("bigChangePct"), "chipWeeks": chip.get("weeks"),
        "revYoy": rev.get("yoy"), "revYm": rev.get("ym"),
        "epsQ": ({"label": single["label"], "eps": single["eps"], "margin": single["margin"]} if single else None),
        "ck": {"fund": scores.get("fund"), "chip": scores.get("chip"), "tech": scores.get("tech"),
               "overall": scores.get("overall"), "label": scores.get("overallLabel")} if scores else None,
        "hits": ctx["hits"].get(code, 0),
        "lastIn": (ctx["members"][day].get(code) or {}).get("last"), "poolDays": (ctx["members"][day].get(code) or {}).get("days", 0),
        "inPoolToday": code in ctx["poolToday"],
    }
    below = bool(ma20 and close < ma20)
    item["below"] = below
    item["high"] = bool(not below and ((dist20 is not None and dist20 >= HIGH_DIST) or (ret20 is not None and ret20 >= HIGH_RET20)))
    item["lit"] = bool(chg is not None and chg >= LIT_PCT)
    item["brk3"] = brk3
    item["star"] = "★" if item["ck"] and item["ck"]["label"] == "強" else "☆" if item["ck"] and item["ck"]["label"] == "中上" else ""
    item["crossing"] = bool(below and dist20 is not None and abs(dist20) <= CROSS_MAX)
    ck3 = [item["ck"][k] for k in ("fund", "chip", "tech")] if item["ck"] else []
    item["danger"] = bool(ck3 and all(v is not None and v < DANGER_BELOW for v in ck3))
    item["fundOk"] = bool(item["ck"] and item["ck"]["fund"] is not None and item["ck"]["fund"] >= HOMEWORK_FUND_MIN
                          and not (single and single["eps"] is not None and single["eps"] < 0))
    item["chipOk"] = item["chipWeek"] is None or item["chipWeek"] >= 0
    item["homework"] = bool(not below and not item["high"] and item["score"] >= HOMEWORK_MIN_SCORE and item["chipOk"] and item["fundOk"])
    return item


def _removed_reason(item: dict[str, Any], last_in: str | None, seeded: bool = False) -> list[str]:
    """上一份名單有、這份沒有的：為什麼被刪（名單規則＝超過 20 個交易日沒再進觀察池；後面列今天的狀況給你判斷刪得對不對）。
    上一份是老師公布的名單（seeded）時，他是自己挑的、不是看「多久沒進池」，所以只列今天的狀況。"""
    reasons = [] if seeded else [f"近 {LIST_DAYS} 個交易日沒再進觀察池" + (f"（最後一次 {last_in[5:].replace('-', '/')}）" if last_in else "")]
    if item["below"]:
        reasons.append("月線下")
    if item["brk3"]:
        reasons.append("破三K低")
    if item["inst10"] is not None and item["inst10"] < 0:
        reasons.append("法人連賣" if item["inst10"] <= -1000 else "法人賣超")
    if item["chipWeek"] is not None and item["chipWeek"] < 0:
        reasons.append("籌碼轉弱")
    if item["score"] < 8:
        reasons.append(f"均線分 {item['score']}")
    if seeded and item["fundOk"] is False and item["ck"] and item["ck"]["fund"] is not None and item["ck"]["fund"] < HOMEWORK_FUND_MIN:
        reasons.append(f"基本面弱（健診基本 {item['ck']['fund']}）")
    return reasons or ["老師從名單移出"]


# ------------------------------------------------------------------ 載入各面向資料

def _institutional(day: str, codes: list[str]) -> tuple[dict[str, dict[str, float]], list[str]]:
    """{代號: {inst5, inst10（三大法人合計，張）}}，往前取最近 10 個有資料的交易日（不超過 day）。"""
    from chips_daily import _institutional_by_date

    with get_connection() as connection:
        rows = connection.execute(
            "SELECT DISTINCT trade_date FROM institutional_daily WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 10", (day,)).fetchall()
    days = [str(r["trade_date"]) for r in rows]        # 新到舊
    if not days:
        return {}, []
    by_date = _institutional_by_date(days, codes)
    out: dict[str, dict[str, float]] = {}
    for code in codes:
        vals = [(by_date.get(d, {}).get(code) or {}).get("total") for d in days]
        if all(v is None for v in vals):
            continue
        s5 = sum(v for v in vals[:5] if v is not None)
        s10 = sum(v for v in vals if v is not None)
        out[code] = {"inst5": round(s5 / 1000), "inst10": round(s10 / 1000)}
    return out, sorted(days)


def _safe(fn, default, label: str):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        logger.exception("[ObsWeekly] %s 讀不到", label)
        return default


def _load_context(day: str, dates: list[str]) -> dict[str, Any]:
    groups_of, names = _universe()
    for code in sorted({c for v in TEACHER_LISTS.values() for c in v["codes"]}):   # 老師名單裡不在我們族群表的股票（威鋒、亞通…）也要讀
        if code not in groups_of:
            groups_of[code] = []
            names.setdefault(code, _stock_name(code))
    codes = sorted(groups_of)
    lists = _weekly_list_dates(dates, day)
    need_days = set()
    for d in [day, *lists]:
        i = dates.index(d)
        need_days.update(dates[max(0, i - (LIST_DAYS + POOL_DAYS - 2)):i + 1])
    i = dates.index(day)
    need_days.update(dates[max(0, i - SERIES_DAYS + 1):i + 1])
    start = min(dates.index(d) for d in need_days)
    start = min(start, max(0, i - MA_FALLBACK_DAYS))     # 日K表讀不到時用黑龍表的收盤算月線，要連續的 20 天以上
    days = dates[start:i + 1]
    rows = _load_days(days, codes)
    return {"groupsOf": groups_of, "names": names, "codes": codes, "lists": lists, "rows": rows, "dates": dates}


def _fill_ctx(ctx: dict[str, Any], day: str, pools: dict[str, dict[str, int]], latest: str) -> None:
    """名單裡（這份、上週最後一份）每一檔要用到的日K、法人、集保、營收、季報、健診。"""
    from financials import all_singles
    from fundamentals_daily import latest_revenue, tdcc_summary
    from stock_checkup import load_rows

    codes = sorted({c for p in pools.values() for c in p})
    since = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=MA_BAR_DAYS)).strftime("%Y-%m-%d")
    ctx["bars"] = _safe(lambda: _load_bars(codes, since=since, until=day, adjusted=True), {}, "日K")
    inst, inst_days = _safe(lambda: _institutional(day, codes), ({}, []), "法人")
    ctx["inst"], ctx["instDays"] = inst, inst_days
    ctx["tdcc"] = _safe(lambda: tdcc_summary(codes, 6), {}, "集保週") if day == latest else {}
    ctx["revenue"] = _safe(lambda: latest_revenue(codes), {}, "月營收")
    singles = _safe(all_singles, {}, "季報")
    ctx["singles"] = {c: singles.get(c) or [] for c in codes}
    ctx["checkup"] = _safe(lambda: load_rows(day, codes), {}, "健診")


# ------------------------------------------------------------------ 段落

def _fmt_lots(n: float | None) -> str:
    return "—" if n is None else f"{n:+,.0f}"


def _group_overview(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """族群總覽：不到 2 檔的併進「其他」（老師那份也是一堆小族群放其他）；檔數多的在前、其他放最後。"""
    count: dict[str, int] = {}
    for it in items:
        count[it["grp"]] = count.get(it["grp"], 0) + 1
    for it in items:
        it["gname"] = it["grp"] if count[it["grp"]] >= SMALL_GROUP else "其他"
    by: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by.setdefault(it["gname"], []).append(it)
    rows = []
    for name, members in by.items():
        have_ma = [m for m in members if m["ma20"]]
        above = sum(1 for m in have_ma if not m["below"])
        rev = _median([m["revYoy"] for m in members if m["revYoy"] is not None])
        inst5 = sum(m["inst5"] for m in members if m["inst5"] is not None) if any(m["inst5"] is not None for m in members) else None
        rows.append({
            "name": name, "n": len(members), "above": above, "withMa": len(have_ma),
            "ma10": sum(1 for m in members if m["score"] >= 10), "revMedian": rev,
            "chip2": sum(1 for m in members if (m["chipWeeks"] or 0) >= 2), "inst5": inst5,
            "codes": [m["code"] for m in members],
        })
    rows.sort(key=lambda g: (g["name"] == "其他", -g["n"], g["name"]))
    revs = [g["revMedian"] for g in rows if g["revMedian"] is not None and g["name"] != "其他"]
    best_rev = max(revs) if revs else None
    for g in rows:
        members = by[g["name"]]
        parts = []
        if g["withMa"]:
            below = g["withMa"] - g["above"]
            if below == 0:
                parts.append("全族站上月線")
            elif below * 2 >= g["withMa"]:
                parts.append(f"{below}/{g['withMa']} 檔在月線下")
            else:
                parts.append(f"{g['above']}/{g['withMa']} 檔站上月線")
        if g["revMedian"] is not None and best_rev is not None and g["revMedian"] == best_rev and best_rev >= 20 and g["name"] != "其他":
            parts.append(f"營收年增中位 {g['revMedian']:+.1f}% 全部名單最強")
        if g["inst5"] is not None and abs(g["inst5"]) >= 1000:
            parts.append(f"法人 5 日{'買超' if g['inst5'] > 0 else '賣超'} {abs(g['inst5']):,.0f} 張")
        if len(members) >= 3:
            top = max(members, key=lambda m: m["score"])
            low = [m for m in members if m["below"] and m["dist20"] is not None]
            low.sort(key=lambda m: m["dist20"])
            if top["score"] >= 10:
                parts.append(f"均線分最高：{top['name']} {top['score']}")
            if low and g["above"] < g["withMa"]:
                parts.append("月線下較深：" + "、".join(m["name"] for m in low[:2]))
        g["note"] = "；".join(parts) if parts else "強弱不一，要逐檔看"
    return rows


def _section_lists(items: list[dict[str, Any]], groups: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    order = {g["name"]: i for i, g in enumerate(groups)}
    key = lambda it: (order.get(it["gname"], 99), -it["score"], it["code"])  # noqa: E731
    below = sorted((i for i in items if i["below"]), key=lambda i: (not i["crossing"], -(i["dist20"] if i["dist20"] is not None else -999), i["code"]))
    high = sorted((i for i in items if i["high"]), key=key)
    homework = sorted((i for i in items if i["homework"]), key=lambda i: (-i["score"], key(i)))
    danger = sorted((i for i in items if i["danger"]), key=key)
    placed = {i["code"] for i in below} | {i["code"] for i in high} | {i["code"] for i in homework}
    watch = sorted((i for i in items if i["code"] not in placed), key=key)
    return {"homework": homework, "high": high, "below": below, "danger": danger, "watch": watch}


def _backtest(day: str, pools: dict[str, dict[str, int]], lists: list[str], ctx: dict[str, Any]) -> dict[str, Any]:
    rows_by_day, dates, bars = ctx["rows"], ctx["dates"], ctx["bars"]
    names, groups_of = ctx["names"], ctx["groupsOf"]

    def close_on(code: str, d: str) -> float | None:
        series = _closes(code, bars, rows_by_day, dates, d)
        return series[-1][1] if series and series[-1][0] == d else None

    def ma20_on(code: str, d: str) -> float | None:
        closes = [c for _x, c in _closes(code, bars, rows_by_day, dates, d)]
        return sum(closes[-20:]) / 20 if len(closes) >= 20 else None

    def returns(members: list[str], start: str) -> dict[str, float]:
        out = {}
        for c in members:
            r = _pct(close_on(c, day), close_on(c, start))
            if r is not None:
                out[c] = r
        return out

    weeks = []
    for L in lists:
        members = sorted(pools.get(L) or {})
        rets = returns(members, L)
        if not rets:
            continue
        keep = [c for c in rets if (m := ma20_on(c, day)) is not None and (close_on(c, day) or 0) >= m]
        have_ma = [c for c in rets if ma20_on(c, day) is not None]
        by_group: dict[str, list[float]] = {}
        for c, r in rets.items():
            by_group.setdefault(_primary_group(c, groups_of), []).append(r)
        group_rows = sorted(({"name": g, "median": _median(v), "n": len(v)} for g, v in by_group.items() if len(v) >= SMALL_GROUP),
                            key=lambda x: -x["median"])
        ordered = sorted(rets.items(), key=lambda kv: -kv[1])
        mk = lambda kv: {"code": kv[0], "name": names.get(kv[0], kv[0]), "ret": kv[1]}  # noqa: E731
        vals = list(rets.values())
        weeks.append({
            "date": L, "n": len(members), "counted": len(rets), "median": _median(vals), "mean": _mean(vals),
            "winRate": round(sum(1 for v in vals if v > 0) / len(vals) * 100), "keepMa": round(len(keep) / len(have_ma) * 100) if have_ma else None,
            "groups": group_rows[:5], "best": [mk(kv) for kv in ordered[:5]], "worst": [mk(kv) for kv in ordered[::-1][:5]],
        })
    after = []
    chain = list(reversed(lists))       # 舊到新
    for older, newer in zip(chain, chain[1:]):
        gone = sorted(set(pools.get(older) or {}) - set(pools.get(newer) or {}))
        rets = returns(gone, newer)
        if not rets:
            continue
        vals = list(rets.values())
        ordered = sorted(rets.items(), key=lambda kv: -kv[1])
        after.append({
            "date": newer, "from": older, "n": len(gone), "counted": len(rets), "median": _median(vals), "mean": _mean(vals),
            "upShare": round(sum(1 for v in vals if v > 0) / len(vals) * 100),
            "items": [{"code": c, "name": names.get(c, c), "ret": r} for c, r in ordered],
        })
    weeks.reverse()     # 舊到新（老師頁 10/04、10/08、10/16、10/22 的順序）
    return {"weeks": weeks, "removedAfter": after}


def _reminders(items: list[dict[str, Any]], sections: dict[str, list[dict[str, Any]]], groups: list[dict[str, Any]], diff: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    n = len(items)
    below = [i for i in items if i["below"]]
    line = f"名單 {n} 檔裡 {len(below)} 檔仍在月線下"
    weak = [g for g in groups if g["withMa"] >= 3 and (g["withMa"] - g["above"]) * 2 >= g["withMa"] and g["name"] != "其他"]
    if weak:
        line += "；" + "、".join(f"{g['name']}族 {g['withMa'] - g['above']}/{g['withMa']} 月線下" for g in weak[:3])
    out.append(line + " → 月線下的先不做功課，等站回月線＋黑K。")
    hw = sections["homework"]
    single = []
    others = [i for i in items if not i["homework"]]
    buy = max((i for i in others if i["inst5"] is not None), key=lambda i: i["inst5"], default=None)
    if buy and buy["inst5"] > 0:
        single.append(f"{buy['name']} 法人大買")
    lit = next((i for i in others if i["lit"]), None)
    if lit:
        single.append(f"{lit['name']} 漲停")
    out.append(f"三者同時成立才上功課：均線分 ≥{HOMEWORK_MIN_SCORE}、籌碼不為負、基本面有撐（健診基本面 ≥{HOMEWORK_FUND_MIN}、最新一季 EPS 不是虧損），而且在月線上、不在高檔。"
               f"目前符合 {len(hw)} 檔" + ("：" + "、".join(i["name"] for i in hw[:8]) if hw else "") + "。"
               + (f"單一指標再強（{'、'.join(single)}）都不算。" if single else ""))
    high = sections["high"]
    if high:
        weak_high = [i for i in high if not i["fundOk"] or (i["epsQ"] and i["epsQ"]["eps"] is not None and i["epsQ"]["eps"] < 0)]
        out.append(f"高檔 ▲ {len(high)} 檔只能等黑再看" + (f"，其中 {'、'.join(i['name'] for i in weak_high[:5])} 基本面偏弱（營收衰退、虧損或沒有健診資料），部位要更小。" if weak_high else "。"))
    odd = sorted((i for i in items if i["chipWeek"] is not None and abs(i["chipWeek"]) >= CHIP_ABNORMAL), key=lambda i: -abs(i["chipWeek"]))
    if odd:
        out.append("籌碼異常待確認：" + "、".join(f"{i['name']}集保 {i['chipWeek']:+.1f}%" for i in odd[:3]) + " 數值異常，可能是股本結構變動，下週看是否回到正常。")
    sellers = sorted((i for i in items if i["inst10"] is not None and i["inst10"] < 0), key=lambda i: i["inst10"])[:3]
    if sellers:
        out.append("法人 10 日大賣三檔：" + "、".join(f"{i['name']} {_fmt_lots(i['inst10'])}" for i in sellers) + "（張）→ 被法人調節中，不搶反彈。")
    cands = []
    for i in items:
        why = []
        if i["epsQ"] and i["epsQ"]["eps"] is not None and i["epsQ"]["eps"] < 0:
            why.append("虧損")
        if i["score"] <= 3:
            why.append(f"均線 {i['score']}")
        if i["ck"] and i["ck"]["fund"] is not None and i["ck"]["fund"] < 20:
            why.append("基本面 <20 分")
        if i["revYoy"] is not None and i["revYoy"] < 0 and i["chipWeek"] is not None and i["chipWeek"] < 0:
            why.append("營收負＋籌碼負")
        if why:
            cands.append((i["name"], "＋".join(why)))
    if cands:
        out.append("刪除候選（供下週決定）：" + "、".join(f"{a}（{b}）" for a, b in cands[:8]) + "。")
    cross = [i for i in sections["below"] if i["crossing"]]
    if cross:
        out.append(f"即將穿越（月線下、距月線 ≤{CROSS_MAX:.0f}%）{len(cross)} 檔：" + "、".join(f"{i['name']}（月線 {i['ma20']:g}）" for i in cross[:6])
                   + "。特別注意，或用條件單在月線價位設警示提醒穿越的位置，有機會買在起漲點；盤只要轉多，通常就是最低點，進場後通常也是最低點。")
    if diff and diff["added"]:
        out.append(f"新進 {len(diff['added'])} 檔、刪除 {len(diff['removed'])} 檔（對 {diff['base'][5:].replace('-', '/')} 名單）；新進的先看位置，月線下或高檔的不追。")
    return out


# ------------------------------------------------------------------ 對外

def build(date: str | None = None) -> dict[str, Any]:
    """date＝名單日（YYYY-MM-DD），不給＝最新一個交易日收盤。"""
    dates = _dates()
    if not dates:
        raise LookupError("還沒有均線分數（收盤後整表完才有）")
    latest = dates[-1]
    day = date or latest
    if day not in dates:
        raise LookupError(f"{day} 沒有資料（只留最近 {len(dates)} 個交易日）")
    if dates.index(day) < POOL_DAYS - 1:
        raise LookupError(f"{day} 之前的資料不到 {POOL_DAYS} 個交易日，算不出觀察池")
    key = f"{day}|{latest}|{len(dates)}"
    with _lock:
        hit = _cache.get(day)
    if hit and hit[0] == key and time.time() - hit[1] < CACHE_SECONDS:
        return hit[2]

    ctx = _load_context(day, dates)
    lists = [d for d in ctx["lists"] if dates.index(d) >= POOL_DAYS - 1]
    members = {d: list_at(d, dates, ctx["rows"]) for d in [day, *lists]}
    pools = members       # 名單（_fill_ctx、回測、異動都用這份）
    ctx["members"] = members
    ctx["poolToday"] = pool_at(day, dates, ctx["rows"])
    ctx["hits"] = hits_at(day, dates, ctx["rows"])
    _fill_ctx(ctx, day, pools, latest)

    seed = seed_at(day)
    seed_info = seed[1] if seed and seed[0] == day else None     # 名單日當天才套老師的族群歸類、段落；之後的日子只用他的名單當基準
    items = [_stock_item(c, day, ctx) for c in sorted(pools[day]) if c in ctx["rows"][day]]
    if seed_info and seed_info.get("gname"):
        for it in items:
            it["grp"] = seed_info["gname"].get(it["code"], it["grp"])
    groups = _group_overview(items)
    if seed_info and seed_info.get("groups"):
        order = {name: i for i, name in enumerate(seed_info["groups"])}
        groups.sort(key=lambda g: order.get(g["name"], 99))
    sections = _section_lists(items, groups)
    if seed_info and seed_info.get("sections"):
        by_code = {it["code"]: it for it in items}
        sections = {k: [by_code[c] for c in seed_info["sections"].get(k, []) if c in by_code] for k in sections}
        danger = set(seed_info["sections"].get("danger", []))
        for it in items:
            it["danger"] = it["code"] in danger          # 危險個股的健診三科（基本／籌碼／技術）老師的算法跟我們不一樣，以他的為準

    diff = None
    if lists:
        base = lists[0]
        prev, now = set(pools[base]), set(pools[day])
        removed_items = [_stock_item(c, day, ctx) for c in sorted(prev - now) if c in ctx["rows"][day]]
        for it in removed_items:
            it["reasons"] = _removed_reason(it, (members[base].get(it["code"]) or {}).get("last"), seeded=base in TEACHER_LISTS)
            it["gname"] = it["grp"]
        added = [i for i in items if i["code"] not in prev]
        diff = {"base": base, "added": added, "removed": removed_items, "kept": len(prev & now), "baseCount": len(prev)}
        for i in items:
            i["isNew"] = i["code"] not in prev
    else:
        for i in items:
            i["isNew"] = False

    backtest = _backtest(day, pools, lists, ctx)
    ck_dates = day if ctx["checkup"] else None
    chip_dates = sorted({v.get("date") for v in ctx["tdcc"].values() if v.get("date")})
    payload = {
        "status": "ok", "date": day, "latest": latest, "dates": [d for d in dates[-20:] if dates.index(d) >= POOL_DAYS - 1],
        "n": len(items), "items": items, "groups": groups, "teacher": ({"date": seed[0], "exact": seed[0] == day} if seed else None), "sections": {k: [i["code"] for i in v] for k, v in sections.items()},
        "diff": diff, "backtest": backtest, "reminders": _reminders(items, sections, groups, diff),
        "basis": {
            "close": day, "checkup": ck_dates, "instTo": ctx["instDays"][-1] if ctx["instDays"] else None,
            "chipWeek": chip_dates[-1] if chip_dates else None,
            "revYm": max((i["revYm"] for i in items if i["revYm"]), default=None),
            "season": max((i["epsQ"]["label"] for i in items if i["epsQ"]), default=None),
        },
        "rules": {"listDays": LIST_DAYS, "poolDays": POOL_DAYS, "minHits": POOL_MIN_HITS, "minScore": POOL_MIN_SCORE, "highDist": HIGH_DIST, "highRet20": HIGH_RET20,
                  "crossMax": CROSS_MAX, "dangerBelow": DANGER_BELOW, "homeworkMinScore": HOMEWORK_MIN_SCORE, "homeworkFundMin": HOMEWORK_FUND_MIN},
    }
    with _lock:
        _cache[day] = (key, time.time(), payload)
    return payload
