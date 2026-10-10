"""均線分數排行（2026-10-10 使用者：照莊爸 zhuang.tw/ma「均線分數排行 滿分 15」做，放在下方導覽列）。

分數就是黑龍表 heilong_daily 的官網式 15 分（score2，收盤後整表時算好；站上 6 條均線、創 6 個天期新高、多頭排列 3 分，
三部分另外存在 ma_bits／hi_bits／align_n，燈號用）。這裡只負責排名：
- 個股分數前 60：全部族群成員（不含股期標的）照「總分高→低，同分先比 12 項本體分（站上＋新高），再照股號小到大」排
  （跟莊爸頁一樣；10/08 他的前 60 名就是這個順序）。昨天名次＝前一個交易日同一套排法的名次（不只前 60）；
  連續上榜＝連續幾個交易日都在前 60 名（含今天）；昨天不在前 60 名＝新進榜。
- 族群分數前十大：族群內有分數的成員平均（族內平均分），附檔數、族內最高、當天族群漲幅排名（本站族群成員平均漲跌幅排序，
  莊爸那欄是他機器人的排名）、昨天名次。千元是價格帶分類，不當族群顯示（莊爸頁也藏）。
- 前十名常客：近 N 個交易日，每天取前 K 名，同分並列全部算上榜（不然常常十幾檔 15 分，照名次硬切變成股號小的佔便宜），
  數每檔擠進去幾次；近 5 日那欄看「現在還在不在」。點開看逐日名次。
- 查詢：輸入股號 → 那檔所屬族群的全部成員（依分數排，查的那檔標色）；輸入族群名 → 那個族群。
"""

from __future__ import annotations

import threading
from typing import Any

from database import get_connection, initialize_database
from heilong_backtest import MA_PERIODS, OFFICIAL_HI_PERIODS, _schema as _heilong_schema, collector_status as _heilong_status
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

TOP_N = 60                       # 個股分數前 60
GROUP_TOP = 10                   # 族群分數前十大
LIST_DATES = 20                  # 下拉可以選最近幾個交易日
LOAD_DAYS = 130                  # 載入最近幾個交易日（連續上榜往回數、前十名常客最多回看 60 天＋選更早的日期）
HIT_DAYS = (10, 20, 40, 60)
HIT_TOPS = (5, 10, 20, 30)
HIT_SHOWS = (20, 30, 50)
RECENT_DAYS = 5                  # 前十名常客「近 5 日」
HIDE_GROUPS = ("千元",)          # 價格帶分類，不當族群顯示

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "data": None}


# ------------------------------------------------------------------ 族群

def _group_lists() -> dict[str, list[str]]:
    """{族群: [代號]}，照族群表順序；不含股期標的。"""
    return {name: [str(c).strip().upper() for c, _n in members] for name, members in STOCK_GROUPS.items() if name not in SPECIAL_GROUP_NAMES}


def _universe() -> tuple[dict[str, list[str]], dict[str, str]]:
    """({代號: [所屬族群，照族群表順序]}, {代號: 股名})：全部族群成員（不含股期標的）。"""
    groups: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for name, members in STOCK_GROUPS.items():
        if name in SPECIAL_GROUP_NAMES:
            continue
        for code, stock_name in members:
            code = str(code).strip().upper()
            groups.setdefault(code, []).append(name)
            names.setdefault(code, str(stock_name))
    return groups, names


def _label(groups: list[str]) -> str:
    return "/".join(g for g in groups if g not in HIDE_GROUPS)


def _stock_name(code: str) -> str:
    try:
        from brew_launch_history import group_and_name
    except Exception:  # noqa: BLE001
        return code
    return group_and_name(code)[1] or code


# ------------------------------------------------------------------ 資料

def _bits(value: Any, size: int) -> list[bool] | None:
    if value is None:
        return None
    return [bool(int(value) >> k & 1) for k in range(size)]


def _record(row) -> dict[str, Any]:
    score = int(row["score2"])
    align = row["align_n"]
    ma = _bits(row["ma_bits"], len(MA_PERIODS))
    hi = _bits(row["hi_bits"], len(OFFICIAL_HI_PERIODS))
    total = sum(ma) + sum(hi) if ma is not None and hi is not None else (score - int(align) if align is not None else score)
    return {
        "sum": score, "total": total, "extra": int(align) if align is not None else score - total,
        "ma": ma, "hi": hi, "close": row["close"], "chg": row["change_pct"],
    }


def _sort_key(code: str, rec: dict[str, Any]) -> tuple:
    return (-rec["sum"], -rec["total"], code)


def _cache_key() -> tuple:
    with get_connection() as connection:
        _heilong_schema(connection)
        row = connection.execute("SELECT MAX(trade_date) AS d, COUNT(*) AS n FROM heilong_daily").fetchone()
    return (row["d"], row["n"])


def _load() -> dict[str, Any]:
    """最近 LOAD_DAYS 個交易日、族群成員的分數，加上每天的排名；黑龍表有變（最新日／筆數）才重讀。"""
    initialize_database()
    key = _cache_key()
    with _lock:
        if _cache["key"] == key and _cache["data"] is not None:
            return _cache["data"]
    groups_of, names = _universe()
    codes = sorted(groups_of)
    with get_connection() as connection:
        dates = [str(r["trade_date"]) for r in connection.execute(
            "SELECT DISTINCT trade_date FROM heilong_daily WHERE score2 IS NOT NULL ORDER BY trade_date DESC LIMIT ?", (LOAD_DAYS,)).fetchall()]
        dates.sort()
        by_date: dict[str, dict[str, dict[str, Any]]] = {d: {} for d in dates}
        if dates:
            for start in range(0, len(codes), 400):
                batch = codes[start:start + 400]
                rows = connection.execute(
                    f"""SELECT trade_date, stock_code, close, change_pct, score2, ma_bits, hi_bits, align_n FROM heilong_daily
                        WHERE trade_date >= ? AND score2 IS NOT NULL AND stock_code IN ({','.join('?' for _ in batch)})""",
                    (dates[0], *batch),
                ).fetchall()
                for r in rows:
                    d = str(r["trade_date"])
                    if d in by_date:
                        by_date[d][str(r["stock_code"]).strip().upper()] = _record(r)
    order: dict[str, list[str]] = {}
    rank: dict[str, dict[str, int]] = {}
    for d, recs in by_date.items():
        ranked = sorted(recs, key=lambda c: _sort_key(c, recs[c]))
        order[d] = ranked
        rank[d] = {c: i + 1 for i, c in enumerate(ranked)}
    data = {"dates": dates, "byDate": by_date, "order": order, "rank": rank, "groupsOf": groups_of, "names": names,
            "groupLists": _group_lists()}
    with _lock:
        _cache.update({"key": key, "data": data})
    return data


def _pick_date(data: dict[str, Any], date: str | None) -> str:
    dates = data["dates"]
    if not dates:
        raise LookupError("還沒有均線分數（收盤後整表完才有）")
    if not date:
        return dates[-1]
    if date not in data["byDate"]:
        raise LookupError(f"{date} 沒有均線分數（只留最近 {len(dates)} 個交易日）")
    return date


def _prev_date(data: dict[str, Any], date: str) -> str | None:
    i = data["dates"].index(date)
    return data["dates"][i - 1] if i > 0 else None


def _streak(data: dict[str, Any], code: str, date: str) -> int:
    dates = data["dates"]
    n = 0
    for d in reversed(dates[:dates.index(date) + 1]):
        r = data["rank"][d].get(code)
        if r is None or r > TOP_N:
            break
        n += 1
    return n


def _row(data: dict[str, Any], code: str, date: str, rec: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = rec or data["byDate"][date][code]
    return {
        "code": code, "name": data["names"].get(code) or _stock_name(code),
        "sum": rec["sum"], "total": rec["total"], "extra": rec["extra"], "ma": rec["ma"], "hi": rec["hi"],
        "close": rec["close"], "chg": rec["chg"], "grp": _label(data["groupsOf"].get(code, [])),
        "rank": data["rank"].get(date, {}).get(code),
    }


# ------------------------------------------------------------------ 族群分數

def _group_table(data: dict[str, Any], date: str) -> list[dict[str, Any]]:
    """每個族群（不含千元）當天的族內平均分、檔數、族內最高、族群漲幅排名；依平均分排好。"""
    recs = data["byDate"].get(date, {})
    out = []
    for name, members in data["groupLists"].items():
        if name in HIDE_GROUPS:
            continue
        scores = [recs[c]["sum"] for c in members if c in recs]
        if not scores:
            continue
        changes = [recs[c]["chg"] for c in members if c in recs and recs[c]["chg"] is not None]
        out.append({"name": name, "avg": round(sum(scores) / len(scores), 2), "n": len(scores), "max": max(scores),
                    "_chg": sum(changes) / len(changes) if changes else None})
    out.sort(key=lambda g: (-g["avg"], -g["max"], g["name"]))
    by_change = sorted((g for g in out if g["_chg"] is not None), key=lambda g: -g["_chg"])
    change_rank = {g["name"]: i + 1 for i, g in enumerate(by_change)}
    for i, g in enumerate(out):
        g["rank"] = i + 1
        g["chgRank"] = change_rank.get(g["name"])
        g["chg"] = round(g.pop("_chg"), 2) if g["_chg"] is not None else g.pop("_chg")
    return out


# ------------------------------------------------------------------ 對外

def ranking(date: str | None = None) -> dict[str, Any]:
    """個股分數前 60＋族群分數前十大。date＝選股日期（YYYY-MM-DD），不給＝最新一天。"""
    data = _load()
    d = _pick_date(data, date)
    prev = _prev_date(data, d)
    prev_rank = data["rank"].get(prev, {}) if prev else {}
    stocks = []
    for code in data["order"][d][:TOP_N]:
        row = _row(data, code, d)
        row["prev"] = prev_rank.get(code)
        row["streak"] = _streak(data, code, d)
        stocks.append(row)
    groups = _group_table(data, d)
    prev_groups = {g["name"]: g["rank"] for g in _group_table(data, prev)} if prev else {}
    for g in groups:
        g["prev"] = prev_groups.get(g["name"])
    status = _heilong_status()
    return {
        "status": "ok", "date": d, "prevDate": prev, "latest": data["dates"][-1],
        "dates": data["dates"][-LIST_DATES:], "n": len(data["byDate"][d]), "updated": status.get("builtAt"),
        "top": TOP_N, "stocks": stocks, "groups": groups[:GROUP_TOP], "groupCount": len(groups),
    }


def hits(days: int = 20, top: int = 10, show: int = 20, date: str | None = None) -> dict[str, Any]:
    """前十名常客：到 date 為止近 days 個交易日，每天取前 top 名（同分並列全部算），列上榜次數最多的 show 檔。"""
    if days not in HIT_DAYS:
        raise ValueError(f"days 只能是 {HIT_DAYS}")
    if top not in HIT_TOPS:
        raise ValueError(f"top 只能是 {HIT_TOPS}")
    if show not in HIT_SHOWS:
        raise ValueError(f"show 只能是 {HIT_SHOWS}")
    data = _load()
    d = _pick_date(data, date)
    end = data["dates"].index(d) + 1
    window = data["dates"][max(0, end - days):end]
    on: dict[str, list[bool]] = {}
    first: dict[str, tuple[int, int]] = {}
    for i, day in enumerate(window):
        ranked = data["order"][day]
        if not ranked:
            continue
        recs = data["byDate"][day]
        cut = recs[ranked[min(top, len(ranked)) - 1]]["sum"]   # 第 top 名的分數；同分的全部算上榜
        for code in ranked:
            if recs[code]["sum"] < cut:
                break
            on.setdefault(code, [False] * len(window))[i] = True
            first.setdefault(code, (i, data["rank"][day][code]))
    recent = max(0, len(window) - RECENT_DAYS)
    counted = sorted(on, key=lambda c: (-sum(on[c]), first[c], c))
    rows = []
    for code in counted[:show]:
        rec = data["byDate"][d].get(code)
        trail = []
        for i, day in enumerate(window):
            day_rec = data["byDate"][day].get(code)
            trail.append([day, data["rank"][day].get(code), day_rec["sum"] if day_rec else None, on[code][i]])
        rows.append({
            "code": code, "name": data["names"].get(code) or _stock_name(code), "hits": sum(on[code]),
            "r5": sum(on[code][recent:]), "sum": rec["sum"] if rec else None, "rank": data["rank"][d].get(code),
            "grp": _label(data["groupsOf"].get(code, [])), "trail": trail,
        })
    return {"status": "ok", "to": d, "from": window[0] if window else None, "ndays": len(window), "days": days, "top": top, "show": show,
            "dates": data["dates"][-LIST_DATES:], "rows": rows}


def _outside_record(code: str, date: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        _heilong_schema(connection)
        row = connection.execute(
            "SELECT close, change_pct, score2, ma_bits, hi_bits, align_n FROM heilong_daily WHERE trade_date = ? AND stock_code = ? AND score2 IS NOT NULL",
            (date, code),
        ).fetchone()
    return _record(row) if row else None


def query(code: str | None = None, group: str | None = None, date: str | None = None) -> dict[str, Any]:
    """輸入股號：那檔所屬族群的全部成員；輸入族群名：那個族群（完全相同優先，再找名字裡有的）。依均線分數排。"""
    data = _load()
    d = _pick_date(data, date)
    lists = data["groupLists"]
    changes = {g["name"]: g["chgRank"] for g in _group_table(data, d)}
    if code:
        code = code.strip().upper()
        names = [g for g in data["groupsOf"].get(code, []) if g not in HIDE_GROUPS] or data["groupsOf"].get(code, [])
        if not names:
            rec = data["byDate"][d].get(code) or _outside_record(code, d)
            if rec is None:
                raise LookupError(f"{code} 沒有 {d} 的均線分數（日K不足 240 根，或不是上市櫃股票）")
            row = _row(data, code, d, rec)
            return {"status": "ok", "date": d, "code": code, "group": row["name"], "groups": [], "rows": [row]}
    else:
        wanted = (group or "").strip()
        if not wanted:
            raise ValueError("請輸入股號或族群名")
        names = [wanted] if wanted in lists else [n for n in lists if wanted in n][:1]
        if not names:
            raise LookupError(f"找不到族群「{wanted}」")
    members: list[str] = []
    for name in names:
        for c in lists.get(name, []):
            if c not in members:
                members.append(c)
    recs = data["byDate"][d]
    rows = [_row(data, c, d) for c in sorted((c for c in members if c in recs), key=lambda c: _sort_key(c, recs[c]))]
    missing = [{"code": c, "name": data["names"].get(c) or c} for c in members if c not in recs]
    return {"status": "ok", "date": d, "code": code, "group": "/".join(names),
            "groups": [{"name": n, "chgRank": changes.get(n)} for n in names], "rows": rows, "missing": missing}
