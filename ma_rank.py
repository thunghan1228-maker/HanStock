"""均線分數排行（2026-10-10 使用者：照老師 zhuang.tw/ma「均線分數排行 滿分 15」做，放在下方導覽列）。

分數就是黑龍表 heilong_daily 的官網式 15 分（score2，收盤後整表時算好；站上 6 條均線、創 6 個天期新高、多頭排列 3 分，
三部分另外存在 ma_bits／hi_bits／align_n，燈號用）。這裡只負責排名：
- 個股分數前 60：全部族群成員（不含股期標的、金融股、艾姆勒）照「總分高→低，同分先比 12 項本體分（站上＋新高），再照股號小到大」排
  （跟老師頁一樣；10/08 他的前 60 名就是這個順序）。昨天名次＝前一個交易日同一套排法的名次（不只前 60）；
  連續上榜＝連續幾個交易日都在前 60 名（含今天）；昨天不在前 60 名＝新進榜。
- 族群分數前十大：族群內有分數的成員平均（族內平均分），附檔數、族內最高、當天族群漲幅排名（本站族群成員平均漲跌幅排序，
  老師那欄是他機器人的排名）、昨天名次。千元是價格帶分類，不當族群顯示（老師頁也藏）。
- 前十名常客：近 N 個交易日，每天取前 K 名，同分並列全部算上榜（不然常常十幾檔 15 分，照名次硬切變成股號小的佔便宜），
  數每檔擠進去幾次；近 5 日那欄看「現在還在不在」。點開看逐日名次。
- 查詢：輸入股號 → 那檔所屬族群的全部成員（依分數排，查的那檔標色）；輸入族群名 → 那個族群。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_connection, initialize_database
from dividend_adjust import adjust as adjust_dividends, score_events as load_dividend_events
from heilong_backtest import (
    LOOKBACK_CALENDAR_DAYS, MA_PERIODS, OFFICIAL_HI_PERIODS, _load_bars, _schema as _heilong_schema, collector_status as _heilong_status, official_parts,
)
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS
from trading_days import is_trading_day

logger = logging.getLogger("hanstock.ma_rank")
TW_TZ = timezone(timedelta(hours=8))

TOP_N = 60                       # 個股分數前 60
GROUP_TOP = 10                   # 族群分數前十大
LIST_DATES = 20                  # 下拉可以選最近幾個交易日
LOAD_DAYS = 130                  # 載入最近幾個交易日（連續上榜往回數、前十名常客最多回看 60 天＋選更早的日期）
HIT_DAYS = (10, 20, 40, 60)
HIT_TOPS = (5, 10, 20, 30)
HIT_SHOWS = (20, 30, 50)
RECENT_DAYS = 5                  # 前十名常客「近 5 日」
HIDE_GROUPS = ("千元", "散裝")   # 千元＝價格帶分類，不當族群顯示；散裝老師放在航運裡、族群分數表沒有（2026-10-29 他的前十大第 2 名是 PA 7.00，我們多一個散裝 7.2）
# 2026-10-10 拿老師 10/08 前 60 名比：他的範圍沒有金融股（富邦金、新光金那些 14 分的都不在），也沒有艾姆勒（籌碼雷達同一份排除）
SKIP_GROUPS = ("金融股",)
SKIP_CODES = frozenset({"2241"})

# 老師 10/22 均線分數頁（全市場 487 檔）沒有、我們的族群表（540 檔）多出來的 18 檔——都是分數 ≥8、本來該進老師前 60 名卻沒出現的（分數不受還原規則影響，
# 所以是老師那天的名單沒有）。2026-10-24 使用者決定名單維持我們的、但要能切換成「對照老師名單」看名次（排行頁的切換鈕）。
# 2026-10-29 老師的範圍長大了（全市場 535 檔已計分，我們 540 檔）：他今天前 60 名出現了佳大、麗臺、玉晶光、新建、振發、碩禾、大立（都從名單拿掉）；
# 我們前 60 名多的易發（10 分）、茂綸（9 分）他沒有，加進來；剩下 11 檔今天分數不夠高，看不出有沒有，先留著。
TEACHER_ABSENT = frozenset({"4720", "5465", "2034", "1735", "1528", "4711", "2406", "2027", "2023", "2032", "3036", "6425", "6227"})

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "data": None}
_teacher_cache: dict[str, Any] = {"src": None, "view": None}


# ------------------------------------------------------------------ 族群

def _group_lists() -> dict[str, list[str]]:
    """{族群: [代號]}，照族群表順序；不含股期標的。"""
    return {name: [c for c in (str(c).strip().upper() for c, _n in members) if c not in SKIP_CODES]
            for name, members in STOCK_GROUPS.items() if name not in SPECIAL_GROUP_NAMES and name not in SKIP_GROUPS}


def _universe() -> tuple[dict[str, list[str]], dict[str, str]]:
    """({代號: [所屬族群，照族群表順序]}, {代號: 股名})：全部族群成員（不含股期標的、金融股、艾姆勒）。"""
    groups: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for name, members in STOCK_GROUPS.items():
        if name in SPECIAL_GROUP_NAMES or name in SKIP_GROUPS:
            continue
        for code, stock_name in members:
            code = str(code).strip().upper()
            if code in SKIP_CODES:
                continue
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
    # 黑龍表整張重算（演算法換新、補了除權息還原）日期和筆數都不變，所以也看「這個程序最近一次建表的時間」，不然這裡的快取會留著舊分數
    return (row["d"], row["n"], _heilong_status().get("builtAt"))


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


def _teacher_view(data: dict[str, Any]) -> dict[str, Any]:
    """拿掉 TEACHER_ABSENT 之後重排的同一份資料：名次、族群平均都照剩下的算（跟老師那天的名單比）。同一份 data 只算一次。"""
    with _lock:
        if _teacher_cache["src"] is data:
            return _teacher_cache["view"]
    gone = TEACHER_ABSENT
    order = {d: [c for c in lst if c not in gone] for d, lst in data["order"].items()}
    view = {**data, "order": order, "rank": {d: {c: i + 1 for i, c in enumerate(lst)} for d, lst in order.items()},
            "byDate": {d: {c: r for c, r in recs.items() if c not in gone} for d, recs in data["byDate"].items()},
            "groupLists": {n: [c for c in ms if c not in gone] for n, ms in data["groupLists"].items()}}
    with _lock:
        _teacher_cache.update({"src": data, "view": view})
    return view


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


def _attach_bias(rows: list[dict[str, Any]], date: str, live: bool = False) -> None:
    """表格列多兩欄（2026-10-23 使用者：照老師頁）：月乖離＝收盤 ÷ 20 日均線 − 1、季乖離＝收盤 ÷ 60 日均線 − 1（%，取到小數 1 位）。
    均線用官方還原日K的收盤算（含當天）；日K不夠、當天沒日K就留 None，畫面顯示「—」。只算要列出的這幾十檔，讀不到不影響排行。
    live＝盤中即時（2026-10-24）：今天還沒有日K，用那一列的現價（row["close"]）當今天收盤接在昨天之後算。"""
    codes = sorted({r["code"] for r in rows})
    if not codes:
        return
    try:
        since = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=130)).strftime("%Y-%m-%d")
        bars = _load_bars(codes, since=since, until=date, adjusted=True)
    except Exception:  # noqa: BLE001
        logger.exception("[MA Rank] 月季乖離讀不到日K")
        bars = {}
    for row in rows:
        series = bars.get(row["code"]) or []
        if live:
            closes = [b[4] for b in series if b[0] < date]
            price = row.get("close")
            if price and float(price) > 0:
                closes.append(float(price))
            ok = bool(series) and len(closes) > 0 and bool(price)
        else:
            closes = [b[4] for b in series if b[0] <= date]
            ok = bool(series) and series[-1][0] == date
        for key, period in (("b20", 20), ("b60", 60)):
            row[key] = round((closes[-1] / (sum(closes[-period:]) / period) - 1) * 100, 1) if ok and len(closes) >= period else None


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

def _view(date: str | None, teacher: bool) -> tuple[dict[str, Any], bool]:
    """(要排的資料, 是不是盤中即時)。沒指定日期、或指定的就是「今天盤中」那一天，而且今天盤中有即時分數（_live_data）→ 用現價當今天收盤的那份；
    其他（回看舊日期、收盤後、休市日）照收盤整表。teacher＝再拿掉老師名單沒有的 18 檔。"""
    data = _load()
    live = _live_data(data)
    use = live is not None and (not date or date == live["dates"][-1])
    src = live if use else data
    return (_teacher_view(src) if teacher else src), use


def ranking(date: str | None = None, teacher: bool = False) -> dict[str, Any]:
    """個股分數前 60＋族群分數前十大。date＝選股日期（YYYY-MM-DD），不給＝最新一天。teacher＝拿掉老師名單沒有的 18 檔再排。
    2026-10-24 使用者：盤中也要即時——沒指定日期、今天又在盤中，就用現價當今天收盤重算（live＝True、date＝今天、asOf＝報價時間），不等收盤後整表。"""
    data, live = _view(date, teacher)
    d = _pick_date(data, date)
    prev = _prev_date(data, d)
    prev_rank = data["rank"].get(prev, {}) if prev else {}
    stocks = []
    for code in data["order"][d][:TOP_N]:
        row = _row(data, code, d)
        row["prev"] = prev_rank.get(code)
        row["streak"] = _streak(data, code, d)
        stocks.append(row)
    _attach_bias(stocks, d, live=live)
    groups = _group_table(data, d)
    prev_groups = {g["name"]: g["rank"] for g in _group_table(data, prev)} if prev else {}
    for g in groups:
        g["prev"] = prev_groups.get(g["name"])
    status = _heilong_status()
    out = {
        "status": "ok", "date": d, "prevDate": prev, "latest": data["dates"][-1],
        "dates": data["dates"][-LIST_DATES:], "n": len(data["byDate"][d]), "updated": status.get("builtAt"),
        "top": TOP_N, "stocks": stocks, "groups": groups[:GROUP_TOP], "groupCount": len(groups),
        "teacher": bool(teacher), "teacherAbsent": len(TEACHER_ABSENT),
    }
    if live:
        out.update({"live": True, "asOf": data.get("liveAt")})
    return out


# ------------------------------------------------------------------ 今日強勢股／弱勢股

STRONG_MIN = 12                  # 強勢股：均線分數 ≥12（2026-10-12 使用者圈老師被動元件那族：天二 15、光頡 13、華新科／千如／勤凱／金山電 12，九豪 11 不圈）
WEAK_MAX = 3                     # 弱勢股：均線分數 ≤3（倒過來：滿分 15 的最低 4 格）


# ------------------------------------------------------------------ 盤中即時分數（2026-10-19）
# 2026-10-19 使用者：今日強勢股盤中也要用即時行情算——均線分數、族群分數、族群名次都用現價當今天收盤重算，不要等收盤後整表。
# 做法跟收盤整表同一套（official_parts：站上 6 條均線＋創 6 個天期新高＋多頭排列），只是最後一根收盤換成現價；
# 前面的日K用官方還原日K（到昨天），一天只讀一次；現價用報價中心（group_quotes_payload，跟首頁同一份、各處共用快取）。
# 收盤後整表（heilong_daily 有今天）進來就以正式的為準，不再算即時。
LIVE_TTL_SECONDS = 45            # 同一份即時結果用 45 秒（首頁每 15 秒問一次、各人共用）
LIVE_FAIL_SECONDS = 20           # 算失敗（沒報價等）20 秒內不重算，直接用收盤整表的
LIVE_START = (9, 5)              # 09:05 以後才有今天的成交價（開盤前報價中心回的是昨天）
LIVE_MIN_QUOTE_RATIO = 0.5       # 今天的報價要有一半以上的成員，太少（被擋、剛開盤）不算
_live_lock = threading.Lock()
_live_cache: dict[str, Any] = {"day": None, "base": None, "at": 0.0, "data": None}
_live_hist: dict[str, Any] = {"day": None, "closes": {}}


def _tw_now() -> datetime:
    return datetime.now(TW_TZ)


def _history_closes(codes: list[str], day: str) -> dict[str, list[float]]:
    """{代號: 官方還原日K收盤（舊到新，到昨天）}，一天只讀一次。"""
    if _live_hist["day"] == day and _live_hist["closes"]:
        return _live_hist["closes"]
    since = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    until = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    loaded = _load_bars(codes, since=since, until=until, adjusted=True)
    dividends = load_dividend_events(loaded.keys())      # 均線分數用除權息還原價（跟收盤整表同一套，見 dividend_adjust）
    closes = {code: [bar[4] for bar in (adjust_dividends(bars, dividends[code]) if code in dividends else bars)] for code, bars in loaded.items()}
    _live_hist.update({"day": day, "closes": closes})
    return closes


def _build_live(data: dict[str, Any], day: str) -> dict[str, Any] | None:
    from brew_launch_history import GROUP_QUOTES_MAX_CODES, group_quotes_payload

    codes = sorted(data["groupsOf"])
    quotes: dict[str, dict[str, Any]] = {}
    quote_time = None
    for start in range(0, len(codes), GROUP_QUOTES_MAX_CODES):
        payload = group_quotes_payload(",".join(codes[start:start + GROUP_QUOTES_MAX_CODES]))
        if payload.get("quoteDate") != day:        # 報價還是昨天的（開盤前、休市）：不算
            return None
        quotes.update(payload.get("quotes") or {})
        quote_time = payload.get("quoteTime") or quote_time
    if len(quotes) < max(1, int(len(codes) * LIVE_MIN_QUOTE_RATIO)):
        return None
    history = _history_closes(codes, day)
    recs: dict[str, dict[str, Any]] = {}
    for code, quote in quotes.items():
        hist, price = history.get(code), quote.get("price")
        if not hist or not price or float(price) <= 0:
            continue
        parts = official_parts(list(hist) + [float(price)])
        if parts is None:                          # 日K不足 240 根（新上市等）
            continue
        ma_bits, hi_bits, align = parts
        ma = [bool(ma_bits >> k & 1) for k in range(len(MA_PERIODS))]
        hi = [bool(hi_bits >> k & 1) for k in range(len(OFFICIAL_HI_PERIODS))]
        total = sum(ma) + sum(hi)
        recs[code] = {"sum": total + align, "total": total, "extra": align, "ma": ma, "hi": hi, "close": float(price), "chg": quote.get("changePercent")}
    if not recs:
        return None
    ranked = sorted(recs, key=lambda c: _sort_key(c, recs[c]))
    return {**data, "dates": data["dates"] + [day], "byDate": {**data["byDate"], day: recs},
            "order": {**data["order"], day: ranked}, "rank": {**data["rank"], day: {c: i + 1 for i, c in enumerate(ranked)}},
            "liveAt": quote_time}


def _live_data(data: dict[str, Any]) -> dict[str, Any] | None:
    """今天盤中的即時分數（跟 _load 同一個結構，多一個「今天」）；不是交易日、還沒開盤、收盤整表已經有今天、報價不夠就回 None。"""
    now = _tw_now()
    day = now.date().isoformat()
    if not data["dates"] or day in data["dates"] or not is_trading_day(now) or (now.hour, now.minute) < LIVE_START:
        return None
    base = data["dates"][-1]
    with _live_lock:
        cache = _live_cache
        age = time.time() - cache["at"]
        if cache["day"] == day and cache["base"] == base and age < (LIVE_TTL_SECONDS if cache["data"] is not None else LIVE_FAIL_SECONDS):
            return cache["data"]
        try:
            live = _build_live(data, day)
        except Exception:  # noqa: BLE001
            logger.exception("[MA Rank] 盤中即時分數算不出來，先用收盤整表")
            live = None
        cache.update({"day": day, "base": base, "at": time.time(), "data": live})
        return live


def _strength_from(data: dict[str, Any], d: str, side: str) -> dict[str, Any]:
    recs = data["byDate"][d]
    table = _group_table(data, d)
    picked = table[:GROUP_TOP] if side == "strong" else list(reversed(table[-GROUP_TOP:]))
    ok = (lambda v: v >= STRONG_MIN) if side == "strong" else (lambda v: v <= WEAK_MAX)
    groups = []
    for g in picked:
        members = [c for c in dict.fromkeys(data["groupLists"].get(g["name"], [])) if c in recs and ok(recs[c]["sum"])]
        members.sort(key=lambda c: _sort_key(c, recs[c]))
        if side == "weak":
            members.reverse()
        rows = [_row(data, c, d, recs[c]) for c in members]
        groups.append({**g, "rows": rows})
    return {
        "status": "ok", "side": side, "date": d, "latest": data["dates"][-1], "groupCount": len(table),
        "rule": {"groups": GROUP_TOP, "min": STRONG_MIN, "max": WEAK_MAX},
        "count": sum(len(g["rows"]) for g in groups), "groups": groups,
    }


def strength(side: str = "strong", date: str | None = None) -> dict[str, Any]:
    """今日強勢股（2026-10-12 使用者：族群分數前十大＋均線分數高，兩個條件過濾之後的股票）／今日弱勢股（倒過來：族群分數倒數十名＋均線分數低）。
    族群照族內平均分排（跟族群分數前十大同一套），族內的股票照分數排。
    2026-10-19：沒指定日期、而且今天盤中時，整份用現價當今天收盤即時重算（live＝True、date＝今天）；收盤整表進來後就是正式的今天。"""
    if side not in ("strong", "weak"):
        raise ValueError("side 只能是 strong 或 weak")
    data = _load()
    if not date:
        live = _live_data(data)
        if live is not None:
            out = _strength_from(live, live["dates"][-1], side)
            out.update({"live": True, "asOf": live.get("liveAt"), "latest": data["dates"][-1]})   # latest＝最新一天正式（收盤整表）的日期
            return out
    return _strength_from(data, _pick_date(data, date), side)


def _hit_counts(data: dict[str, Any], d: str, days: int, top: int) -> tuple[list[str], dict[str, list[bool]], list[str]]:
    """(窗口交易日, {代號: 每天有沒有上榜}, 照上榜次數排好的代號)。同次數：先上榜的先、同一天名次前面的先。"""
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
    return window, on, sorted(on, key=lambda c: (-sum(on[c]), first[c], c))


def regulars(days: int = 20, top: int = 10, date: str | None = None) -> list[tuple[str, int]]:
    """均線常客整張榜（選股系統的雙劍出擊用）：[(代號, 上榜次數)]，照次數排好。date 沒有分數就回空。"""
    data = _load()
    try:
        d = _pick_date(data, date)
    except LookupError:
        return []
    _window, on, counted = _hit_counts(data, d, days, top)
    return [(c, sum(on[c])) for c in counted]


def hits(days: int = 20, top: int = 10, show: int = 20, date: str | None = None, teacher: bool = False) -> dict[str, Any]:
    """前十名常客：到 date 為止近 days 個交易日，每天取前 top 名（同分並列全部算），列上榜次數最多的 show 檔。
    2026-10-24：沒指定日期、今天盤中時，最後一天用現價即時算（live＝True），所以盤中的名單跟著動、不用等收盤。"""
    if days not in HIT_DAYS:
        raise ValueError(f"days 只能是 {HIT_DAYS}")
    if top not in HIT_TOPS:
        raise ValueError(f"top 只能是 {HIT_TOPS}")
    if show not in HIT_SHOWS:
        raise ValueError(f"show 只能是 {HIT_SHOWS}")
    data, live = _view(date, teacher)
    d = _pick_date(data, date)
    window, on, counted = _hit_counts(data, d, days, top)
    recent = max(0, len(window) - RECENT_DAYS)
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
    out = {"status": "ok", "to": d, "from": window[0] if window else None, "ndays": len(window), "days": days, "top": top, "show": show,
           "dates": data["dates"][-LIST_DATES:], "rows": rows}
    if live:
        out.update({"live": True, "asOf": data.get("liveAt")})   # 最後一天是盤中即時分數（還不是收盤）
    return out


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
    _attach_bias(rows, d)
    missing = [{"code": c, "name": data["names"].get(c) or c} for c in members if c not in recs]
    return {"status": "ok", "date": d, "code": code, "group": "/".join(names),
            "groups": [{"name": n, "chgRank": changes.get(n)} for n in names], "rows": rows, "missing": missing}
