"""選股系統・條件選股（2026-10-10 使用者：照莊爸「選股系統」做，併進「個股研究」面板）。

勾要用的條件、拉門檻，列出同時符合的股票；範圍見 SKIP_GROUPS 上面的說明，每一列帶齊各工具的欄位：
- 均線分數 ≥：官網式 15 分（黑龍表 score2，收盤後整表）。
- 籌碼暴增 ≥：籌碼暴增雷達的籌碼%（3√x），用資料日之前最近一週的集保；沒上買超榜（>4）的不算符合，表上淡色顯示實際值。
- 主動式 ETF 持有 ≥：五檔主動式 ETF 在資料日（含）之前最近一份持股裡，有幾檔持有它。
- 法人 3／5 日累積買超・估量 ≥：近 3／5 個交易日三大法人合計買賣超張數 ÷ 同期成交量；要是累積買超（✓）才算。
- 雙劍出擊・兩張榜各取前 N 名：均線常客（近 20 個交易日每天取均線分數前 10 名，同分全算，數上榜天數）
  ∩ 籌碼常客（近 9 週每週取籌碼暴增買超榜前 10 名，數上榜週數），兩張榜都排進前 N 名才算。
- 處置股倒數 ≤：目前在處置中、還要關幾個交易日（處置迄日當天＝1，下一個交易日出關）。
- 有股票期貨／有小型股票期貨：期交所股票期貨標的清單（一般 2,000 股、小型 100 股）。
- 排除處置股：處置中，以及交易所已公告、下一個交易日開始處置的。
- 自己打造 K 棒：資料日那根紅K（收盤＞開盤）／黑K（收盤＜開盤）／不限，加漲跌幅（跟前一天收盤比）區間。
- 河流圖位置：估值河流圖還在做，先不開放。
回測指定日：資料日可以選過去的交易日，名單會帶 D+1／D+2／到最新收盤的表現（價格用還原日K）。
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database
from heilong_backtest import _schema as _heilong_schema
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))

CACHE_SECONDS = 600.0
LIST_DATES = 60                 # 回測可以選最近幾個交易日
CHIP_BUY_MIN = 4.0              # 籌碼暴增買超榜門檻（跟雷達一樣）
SWORD_MA_DAYS, SWORD_MA_TOP = 20, 10
SWORD_CHIP_WEEKS, SWORD_CHIP_TOP = 9, 10
ETF_MAX_GAP_DAYS = 8
HIDE_GROUPS = ("千元",)
# 篩選範圍（2026-10-10 對照莊爸名單）：族群成員，加上有股票期貨、有主動式 ETF 持有、處置中／明天起處置、上籌碼暴增買超榜的；
# 他名單上族群外的（冠西電、天擎、中華電、日月光、泰鼎-KY）都是這幾種。只在金融股族群的、艾姆勒不算（他的範圍沒有）。
SKIP_GROUPS = ("金融股",)
SKIP_CODES = frozenset({"2241"})
TAIFEX_URL = "https://www.taifex.com.tw/cht/2/stockLists"
FUTURES_REFRESH_SECONDS = 12 * 3600
# 期交所抓不到時的備援：小型股票期貨標的（2026-10-10 期交所清單，47 檔）；一般股票期貨用族群表的「股期標的」
MINI_FUTURES_FALLBACK = frozenset((
    "1477", "1519", "1565", "2049", "2059", "2308", "2327", "2330", "2345", "2357", "2360", "2368", "2376", "2379", "2383", "2404",
    "2449", "2454", "3008", "3017", "3034", "3105", "3211", "3293", "3324", "3406", "3443", "3529", "3533", "3653", "3661", "3665",
    "3680", "3711", "5269", "5274", "5904", "6139", "6223", "6472", "6488", "6510", "6526", "6669", "8046", "8299", "9958",
))

PARAM_KEYS = ("score", "chip", "etf", "inst3", "inst5", "sword", "dispo", "fut", "mini", "exdispo", "k", "kmin", "kmax")

_lock = threading.Lock()
_cache: dict[str, Any] = {}
_futures: dict[str, Any] = {"at": 0.0, "std": None, "mini": None, "source": None}


# ------------------------------------------------------------------ 股票期貨標的

def parse_taifex_stock_lists(page: str) -> tuple[set[str], set[str]]:
    """期交所「股票期貨、選擇權商品標的」頁：回 (有股票期貨的代號, 有小型股票期貨的代號)。
    同一檔一般型（2,000 股）跟小型（100 股）各一列，用「標準型證券股數」那欄分。"""
    std: set[str] = set()
    mini: set[str] = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        if len(cells) < 12 or not re.fullmatch(r"\d{4,6}", cells[2]) or "是股票期貨標的" not in cells[4]:
            continue
        shares = cells[11].replace(",", "")
        if shares == "100":
            mini.add(cells[2])
        elif shares == "2000":
            std.add(cells[2])
    return std, mini


def _fetch_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)", "Accept-Language": "zh-TW,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


def futures_sets(fetcher: Callable[[str], str] | None = None) -> tuple[set[str], set[str], str]:
    """(一般股票期貨標的, 小型股票期貨標的, 來源)。半天抓一次期交所；抓不到用族群表的股期標的＋內建小型清單。"""
    with _lock:
        if _futures["std"] is not None and time.time() - _futures["at"] < FUTURES_REFRESH_SECONDS:
            return _futures["std"], _futures["mini"], _futures["source"]
    try:
        std, mini = parse_taifex_stock_lists((fetcher or _fetch_text)(TAIFEX_URL))
        if len(std) < 100:
            raise ValueError(f"期交所清單只有 {len(std)} 檔")
        source = "期交所"
    except Exception as exc:  # noqa: BLE001
        logger.info("taifex stock list unavailable: %s", exc)
        std = {str(c).strip().upper() for c, _n in STOCK_GROUPS.get("股期標的", [])}
        mini, source = set(MINI_FUTURES_FALLBACK), "內建清單"
    with _lock:
        _futures.update({"at": time.time(), "std": std, "mini": mini, "source": source})
    return std, mini, source


# ------------------------------------------------------------------ 各工具的欄位

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


def _heilong_dates() -> list[str]:
    with get_connection() as connection:
        _heilong_schema(connection)
        return [str(r["trade_date"]) for r in connection.execute("SELECT DISTINCT trade_date FROM heilong_daily ORDER BY trade_date").fetchall()]


def _day_rows(day: str) -> dict[str, Any]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT stock_code, open, high, low, close, volume, change_pct, score2 FROM heilong_daily WHERE trade_date = ?", (day,)
        ).fetchall()
    return {str(r["stock_code"]).strip().upper(): r for r in rows}


def _volumes(days: list[str]) -> dict[str, dict[str, int]]:
    """{代號: {日期: 成交張數}}（黑龍表的日K量）。"""
    if not days:
        return {}
    out: dict[str, dict[str, int]] = {}
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT trade_date, stock_code, volume FROM heilong_daily WHERE trade_date IN ({','.join('?' for _ in days)})", tuple(days)
        ).fetchall()
    for r in rows:
        out.setdefault(str(r["stock_code"]).strip().upper(), {})[str(r["trade_date"])] = int(r["volume"] or 0)
    return out


def _institutional(days: list[str]) -> dict[str, dict[str, int]]:
    """{代號: {日期: 三大法人合計買賣超張數}}。"""
    if not days:
        return {}
    out: dict[str, dict[str, int]] = {}
    with get_connection() as connection:
        try:
            rows = connection.execute(
                f"SELECT trade_date, stock_code, total_net FROM institutional_daily WHERE trade_date IN ({','.join('?' for _ in days)})", tuple(days)
            ).fetchall()
        except Exception:  # noqa: BLE001  （法人表還沒建）
            rows = []
    for r in rows:
        out.setdefault(str(r["stock_code"]).strip().upper(), {})[str(r["trade_date"])] = round((r["total_net"] or 0) / 1000)
    return out


def _inst_pct(code: str, days: list[str], inst: dict[str, dict[str, int]], vols: dict[str, dict[str, int]]) -> dict[str, Any] | None:
    per = inst.get(code)
    if not per:
        return None
    net = sum(per.get(d, 0) for d in days)
    vol = sum((vols.get(code) or {}).get(d, 0) for d in days)
    if not vol:
        return None
    return {"net": net, "pct": round(net / vol * 100, 1), "buy": net > 0}


def _etf_counts(day: str) -> tuple[dict[str, int], list[str]]:
    """({代號: 幾檔主動式 ETF 持有}, 用到的那幾份持股日期)：每檔 ETF 取 day（含）之前最近一份（最多隔 8 天）。"""
    try:
        from etf_holdings import ETFS, _schema as _etf_schema
    except Exception:  # noqa: BLE001
        return {}, []
    floor = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=ETF_MAX_GAP_DAYS)).strftime("%Y-%m-%d")
    counts: dict[str, int] = {}
    used: list[str] = []
    with get_connection() as connection:
        _etf_schema(connection)
        for code, _name, _issuer in ETFS:
            row = connection.execute(
                "SELECT MAX(trade_date) AS d FROM etf_snapshots WHERE etf_code = ? AND trade_date <= ? AND trade_date >= ?", (code, day, floor)
            ).fetchone()
            if not row or not row["d"]:
                continue
            used.append(str(row["d"]))
            for h in connection.execute("SELECT stock_code FROM etf_holdings WHERE etf_code = ? AND trade_date = ?", (code, row["d"])):
                stock = str(h["stock_code"]).strip().upper()
                counts[stock] = counts.get(stock, 0) + 1
    return counts, used


def _dispositions(day: str, next_day: str | None) -> tuple[dict[str, int], set[str]]:
    """({處置中代號: 倒數幾個交易日（迄日當天＝1）}, {下一個交易日開始處置、已公告的代號})。"""
    try:
        from disposition_jail import count_trading_days, load_punishes
    except Exception:  # noqa: BLE001
        return {}, set()
    since = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=40)).strftime("%Y-%m-%d")
    jailed: dict[str, int] = {}
    upcoming: set[str] = set()
    for p in load_punishes(since):
        code = str(p["code"]).strip().upper()
        if p["start"] <= day <= p["end"]:
            jailed[code] = max(jailed.get(code, 0), count_trading_days(day, p["end"]))
        elif next_day and p["start"] == next_day and (not p.get("announce") or p["announce"] <= day):
            upcoming.add(code)
    return jailed, upcoming


def _chip_info(day: str) -> tuple[str | None, dict[str, float], dict[str, int], list[str]]:
    """(用到的集保週, {代號: 籌碼%}, {代號: 近 9 週擠進買超榜前 10 名的週數}, 籌碼常客照週數排好的代號)。
    只用資料日之前結算的週（結算日當天晚上才公布）。"""
    try:
        from chip_radar import load_radar
    except Exception:  # noqa: BLE001
        return None, {}, {}, []
    radar = load_radar()
    weeks = [d for d in radar.dates if d < day]           # 新到舊
    if not weeks:
        return None, {}, {}, []
    week = weeks[0]
    chips = {code: per[week] for code, per in radar.chips.items() if week in per}
    counts: dict[str, int] = {}
    latest: dict[str, int] = {}
    for i, w in enumerate(weeks[:SWORD_CHIP_WEEKS]):
        buy, _sell = radar.lists(w)
        for rank, code in enumerate(buy[:SWORD_CHIP_TOP]):
            counts[code] = counts.get(code, 0) + 1
            latest.setdefault(code, i * 100 + rank)
    order = sorted(counts, key=lambda c: (-counts[c], latest[c], c))
    return week, chips, counts, order


def _ma_regulars(day: str) -> tuple[dict[str, int], list[str]]:
    try:
        from ma_rank import regulars
    except Exception:  # noqa: BLE001
        return {}, []
    rows = regulars(SWORD_MA_DAYS, SWORD_MA_TOP, day)
    return {c: n for c, n in rows}, [c for c, _n in rows]


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round((a / b - 1) * 100, 2)


def _facts(day: str, dates: list[str]) -> dict[str, Any]:
    """資料日 day 全市場每一檔的欄位（篩選前）。"""
    i = dates.index(day)
    after = dates[i + 1:]
    rows = _day_rows(day)
    d1 = _day_rows(after[0]) if after else {}
    d2 = _day_rows(after[1]) if len(after) > 1 else {}
    latest_day = dates[-1]
    latest = _day_rows(latest_day) if after else {}
    inst_days = dates[max(0, i - 4):i + 1]
    inst = _institutional(inst_days)
    vols = _volumes(inst_days)
    etf, etf_dates = _etf_counts(day)
    jailed, upcoming = _dispositions(day, after[0] if after else _next_trading(day))
    chip_week, chips, chip_counts, chip_order = _chip_info(day)
    ma_counts, ma_order = _ma_regulars(day)
    std, mini, fut_source = futures_sets()
    groups = _groups_of()
    names = _names()
    out = []
    for code, r in rows.items():
        if not (len(code) == 4 and code.isdigit() and not code.startswith("00")):
            continue
        o, c = float(r["open"] or 0), float(r["close"])
        chip = chips.get(code)
        x1, x2, xl = d1.get(code), d2.get(code), latest.get(code)
        mine = groups.get(code, [])
        skipped = code in SKIP_CODES or (bool(mine) and set(mine) <= set(SKIP_GROUPS))
        in_uni = not skipped and bool(
            [g for g in mine if g not in SKIP_GROUPS] or code in std or code in mini or etf.get(code) or code in jailed or code in upcoming
            or (chip is not None and chip >= CHIP_BUY_MIN))
        out.append({
            "code": code, "name": names.get(code, code), "grp": "/".join(mine), "inUni": in_uni,
            "score": r["score2"], "close": c, "chg": r["change_pct"],
            "k": "red" if c > o else ("black" if c < o else "flat"),
            "chip": round(chip, 2) if chip is not None else None, "chipOn": chip is not None and chip >= CHIP_BUY_MIN,
            "maHits": ma_counts.get(code), "chipHits": chip_counts.get(code),
            "etf": etf.get(code, 0), "dispo": jailed.get(code), "upcoming": code in upcoming,
            "fut": code in std or code in mini, "mini": code in mini, "futLabel": "小期" if code in mini else ("期" if code in std else None),
            "inst3": _inst_pct(code, inst_days[-3:], inst, vols), "inst5": _inst_pct(code, inst_days[-5:], inst, vols),
            "d1": _pct(float(x1["close"]), c) if x1 else None, "d1h": _pct(float(x1["high"]), c) if x1 else None,
            "d2": _pct(float(x2["close"]), c) if x2 else None, "perf": _pct(float(xl["close"]), c) if xl else None,
        })
    return {
        "rows": out, "next": after[:2], "latestDate": latest_day if after else None,
        "chipWeek": chip_week, "etfDates": sorted(set(etf_dates)), "instDays": inst_days, "futSource": fut_source,
        "maOrder": ma_order, "chipOrder": chip_order,
    }


def _next_trading(day: str) -> str | None:
    try:
        from trading_days import next_trading_day
    except Exception:  # noqa: BLE001
        return None
    return next_trading_day(day).isoformat()


# ------------------------------------------------------------------ 篩選

def _num(raw: Any, name: str, *, integer: bool = False) -> float | int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw) if integer else float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必須是數字") from exc


def _flag(raw: Any) -> bool:
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    p: dict[str, Any] = {
        "score": _num(raw.get("score"), "score", integer=True), "chip": _num(raw.get("chip"), "chip"),
        "etf": _num(raw.get("etf"), "etf", integer=True), "inst3": _num(raw.get("inst3"), "inst3"), "inst5": _num(raw.get("inst5"), "inst5"),
        "sword": _num(raw.get("sword"), "sword", integer=True), "dispo": _num(raw.get("dispo"), "dispo", integer=True),
        "fut": _flag(raw.get("fut")), "mini": _flag(raw.get("mini")), "exdispo": _flag(raw.get("exdispo")),
        "k": (raw.get("k") or "any").strip().lower(), "kmin": _num(raw.get("kmin"), "kmin"), "kmax": _num(raw.get("kmax"), "kmax"),
    }
    if p["k"] not in {"any", "red", "black"}:
        raise ValueError("k 只能是 any／red／black")
    return p


def _match(row: dict[str, Any], p: dict[str, Any], sword: tuple[set[str], set[str]] | None) -> bool:
    if p["score"] is not None and (row["score"] is None or row["score"] < p["score"]):
        return False
    if p["chip"] is not None and not (row["chipOn"] and row["chip"] >= p["chip"]):
        return False
    if p["etf"] is not None and row["etf"] < p["etf"]:
        return False
    for key in ("inst3", "inst5"):
        if p[key] is not None and not (row[key] and row[key]["buy"] and row[key]["pct"] >= p[key]):
            return False
    if sword is not None and not (row["code"] in sword[0] and row["code"] in sword[1]):
        return False
    if p["dispo"] is not None and (row["dispo"] is None or row["dispo"] > p["dispo"]):
        return False
    if p["fut"] and not row["fut"]:
        return False
    if p["mini"] and not row["mini"]:
        return False
    if p["exdispo"] and (row["dispo"] is not None or row["upcoming"]):
        return False
    if p["k"] != "any" and row["k"] != p["k"]:
        return False
    if (p["kmin"] is not None or p["kmax"] is not None) and row["chg"] is None:
        return False
    if p["kmin"] is not None and row["chg"] < p["kmin"]:
        return False
    if p["kmax"] is not None and row["chg"] > p["kmax"]:
        return False
    return True


def screen(raw: dict[str, Any] | None = None, date: str | None = None, *, in_universe: bool = True) -> dict[str, Any]:
    """照條件篩出名單。date＝資料日（YYYY-MM-DD，不給＝最新一天）；回 rows（照均線分數高到低）與用到的資料日期。
    in_universe＝只列篩選範圍內的（個股完整彙整查單檔時不限）。"""
    p = normalize(raw or {})
    initialize_database()
    dates = _heilong_dates()
    if not dates:
        raise LookupError("還沒有日K整理好的資料（收盤後整表完才有）")
    day = date or dates[-1]
    if day not in dates:
        raise LookupError(f"{day} 沒有資料（只能選最近 {len(dates)} 個交易日）")
    key = f"{day}|{dates[-1]}|{len(dates)}"
    with _lock:
        hit = _cache.get(day)
    if hit and hit[0] == key and time.time() - hit[1] < CACHE_SECONDS:
        facts = hit[2]
    else:
        facts = _facts(day, dates)
        with _lock:
            _cache[day] = (key, time.time(), facts)
    sword = None
    if p["sword"] is not None:
        sword = (set(facts["maOrder"][:p["sword"]]), set(facts["chipOrder"][:p["sword"]]))
    rows = [r for r in facts["rows"] if (r["inUni"] or not in_universe) and _match(r, p, sword)]
    rows.sort(key=lambda r: (-(r["score"] if r["score"] is not None else -1), r["code"]))
    return {
        "status": "ok", "date": day, "latest": dates[-1], "dates": dates[-LIST_DATES:], "params": p, "count": len(rows),
        "universe": sum(1 for r in facts["rows"] if r["inUni"]),
        "rows": rows, "next": facts["next"], "latestDate": facts["latestDate"], "chipWeek": facts["chipWeek"], "etfDates": facts["etfDates"],
        "instDays": facts["instDays"], "futSource": facts["futSource"],
    }


def stock(code: str, date: str | None = None) -> dict[str, Any]:
    """個股完整彙整：一檔在資料日的全部欄位（不套條件）。"""
    code = str(code or "").strip().upper()
    out = screen({}, date, in_universe=False)
    row = next((r for r in out["rows"] if r["code"] == code), None)
    if row is None:
        raise LookupError(f"{code} 在 {out['date']} 沒有資料")
    return {**{k: v for k, v in out.items() if k not in ("rows", "count", "params")}, "row": row}
