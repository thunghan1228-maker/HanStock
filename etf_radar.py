"""主動式 ETF 持股雷達（2026-10-10 使用者：照莊爸 zhuang.tw/etf 做，併進籌碼日報的「主動式基金」分頁）。

資料來源跟下午報同一份：etf_holdings 每天存的五檔持股快照（統一／復華／群益官方 PCF，排程主機鏡像）。
金額＝增減股數 × 收盤價（黑龍表那天的收盤；張數不能直接比，便宜的股票幾千張也不過幾億）。

- overview：今天五檔合計加碼／出貨前十、這一週（往回 5 個資料日，只比頭尾）合計前十、最多人共同持有、資金潮汐、五檔卡片
- fund：單一檔的最新一日動作、近 5 日累計流向、連續同向動作（次數≠天數）、持股權重前 20
- stock：個股 × 全部主動式 ETF 的逐日進出紀錄＋各檔目前持股、估計成本
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any

from brew_launch_history import group_and_name
from database import get_connection, initialize_database
from etf_holdings import ETFS, ETF_META, FALLBACK_SOURCE, PREV_MAX_GAP_DAYS, _schema

TOP_N = 10            # 五檔合計：加碼／出貨前幾名
COMMON_N = 15         # 最多人共同持有：列幾檔
WEEK_SPAN = 5         # 一週＝往回 5 個資料日
FUND_TOP_N = 20       # 單檔持股權重前幾大
FLOW_ROWS = 15        # 近 5 日累計流向：加減碼合計列幾檔（依增減張數絕對值）
STREAK_MIN = 2        # 連續同向至少幾次
TIDE_DAYS = 5         # 資金潮汐：幾個資料日
TIDE_MAX_STOCKS = 40  # 潮汐最多畫幾顆
STOCK_MONTHS = 3      # 個股進出紀錄只列最近幾個月
CACHE_SECONDS = 300

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "data": None}


# ---- 資料載入（全部快照一次讀進來，五檔 × 幾十天 × 幾十檔，量很小）----

def _load() -> dict[str, Any]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        key_row = connection.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS u FROM etf_snapshots").fetchone()
        key = (int(key_row["n"] or 0), str(key_row["u"] or ""))
        with _lock:
            if _cache["key"] == key and time.time() - _cache["at"] < CACHE_SECONDS:
                return _cache["data"]
        snaps = connection.execute("SELECT etf_code, trade_date, name, issuer, source, nav, units, rows FROM etf_snapshots").fetchall()
        hold = connection.execute("SELECT etf_code, trade_date, stock_code, stock_name, shares, weight_pct FROM etf_holdings").fetchall()
    known = {code for code, _n, _i in ETFS}
    fund_dates: dict[str, list[str]] = {}
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    for r in snaps:
        code = str(r["etf_code"])
        if code not in known:
            continue
        fund_dates.setdefault(code, []).append(str(r["trade_date"]))
        meta[(code, str(r["trade_date"]))] = dict(r)
    for v in fund_dates.values():
        v.sort()
    holdings: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    strong: dict[str, str] = {}
    weak: dict[str, str] = {}
    for r in hold:
        k = (str(r["etf_code"]), str(r["trade_date"]))
        if k not in meta:
            continue
        stock = str(r["stock_code"]).strip().upper()
        holdings.setdefault(k, {})[stock] = {"shares": int(r["shares"]), "weight": r["weight_pct"]}
        nm = str(r["stock_name"] or "").strip()
        if nm:
            # 復華的名稱會截成四個字，統一／群益給的優先
            (weak if meta[k].get("source") == "fhtrust" else strong).setdefault(stock, nm)
    names = {**weak, **strong}
    # 反推價：權重 × 基金淨值 ÷ 股數（黑龍表沒有這檔或那天時才用；PCF 不給價格，幾檔各自反推取中位數）
    implied_lists: dict[str, dict[str, list[float]]] = {}
    for (code, d), h in holdings.items():
        nav = meta[(code, d)].get("nav")
        if not nav:
            continue
        for stock, v in h.items():
            if v["shares"] > 0 and v["weight"] and v["weight"] >= 0.05:
                implied_lists.setdefault(d, {}).setdefault(stock, []).append(v["weight"] / 100 * nav / v["shares"])
    implied = {d: {s: sorted(v)[len(v) // 2] for s, v in m.items()} for d, m in implied_lists.items()}
    all_dates = sorted({d for v in fund_dates.values() for d in v}, reverse=True)
    data = {"fundDates": fund_dates, "meta": meta, "holdings": holdings, "names": names, "dates": all_dates, "closes": {}, "implied": implied}
    with _lock:
        _cache.update({"key": key, "at": time.time(), "data": data})
    return data


def _closes(data: dict[str, Any], date: str) -> tuple[dict[str, float], str | None]:
    """那一天（含）以前最近一個有黑龍表的交易日的全市場收盤；黑龍表沒有的用 ETF 權重反推價補。"""
    if date in data["closes"]:
        return data["closes"][date]
    from heilong_backtest import _schema as _heilong_schema

    with get_connection() as connection:
        _heilong_schema(connection)
        row = connection.execute("SELECT MAX(trade_date) AS d FROM heilong_daily WHERE trade_date <= ?", (date,)).fetchone()
        day = str(row["d"]) if row and row["d"] else None
        rows = connection.execute("SELECT stock_code, close FROM heilong_daily WHERE trade_date = ?", (day,)).fetchall() if day else []
    have = [d for d in data["implied"] if d <= date]
    merged = dict(data["implied"].get(max(have), {})) if have else {}
    merged.update({str(r["stock_code"]).strip().upper(): float(r["close"]) for r in rows if r["close"]})
    out = (merged, day)
    data["closes"][date] = out
    return out


def _name(data: dict[str, Any], stock: str) -> tuple[str, str]:
    group, name = group_and_name(stock)
    if name == stock:
        name = data["names"].get(stock) or stock
    return group, name


def _fund_on(data: dict[str, Any], code: str, date: str) -> str | None:
    """這一檔在 date（含）以前最近的一份。"""
    ds = [d for d in data["fundDates"].get(code, []) if d <= date]
    return ds[-1] if ds else None


def _fund_prev(data: dict[str, Any], code: str, date: str) -> str | None:
    """這一檔在 date 之前最近的一份（最多隔 PREV_MAX_GAP_DAYS 個日曆日，跟下午報一樣）。"""
    floor = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=PREV_MAX_GAP_DAYS)).strftime("%Y-%m-%d")
    ds = [d for d in data["fundDates"].get(code, []) if floor <= d < date]
    return ds[-1] if ds else None


def _hold(data: dict[str, Any], code: str, date: str | None) -> dict[str, dict[str, Any]]:
    return data["holdings"].get((code, date), {}) if date else {}


def _deltas(cur: dict[str, dict[str, Any]], prev: dict[str, dict[str, Any]]) -> dict[str, tuple[int, int, int]]:
    """{股票: (增減股數, 現在股數, 之前股數)}，只留有動的。"""
    out: dict[str, tuple[int, int, int]] = {}
    for stock in set(cur) | set(prev):
        now = cur.get(stock, {}).get("shares", 0)
        before = prev.get(stock, {}).get("shares", 0)
        if now != before:
            out[stock] = (now - before, now, before)
    return out


def _act(delta: int, now: int, before: int) -> str:
    if before == 0:
        return "新進"
    if now == 0:
        return "清空"
    return "加碼" if delta > 0 else "減碼"


def _resolve_date(data: dict[str, Any], as_of: str | None) -> str | None:
    have = [d for d in data["dates"] if not as_of or d <= as_of]
    return have[0] if have else None


def _yi(shares: float, close: float | None) -> float | None:
    return None if close is None else shares * close / 1e8


# ---- 五檔合計 ----

def _combine(per_fund: dict[str, dict[str, tuple[int, int, int]]], closes: dict[str, float], data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    agg: dict[str, dict[str, Any]] = {}
    for fund, moves in per_fund.items():
        for stock, (delta, _now, _before) in moves.items():
            a = agg.setdefault(stock, {"code": stock, "shares": 0, "funds": []})
            a["shares"] += delta
            a["funds"].append({"c": fund, "lots": round(delta / 1000, 1)})
    rows = []
    for stock, a in agg.items():
        if a["shares"] == 0:
            continue
        group, name = _name(data, stock)
        amt = _yi(a["shares"], closes.get(stock))
        rows.append({"code": stock, "name": name, "group": group, "lots": round(a["shares"] / 1000, 1),
                     "amount": round(amt, 2) if amt is not None else None, "funds": a["funds"]})
    # 依金額排；拿不到收盤價的排在有金額的後面、改用張數
    buy = sorted([r for r in rows if r["lots"] > 0], key=lambda r: (r["amount"] is None, -(r["amount"] or 0), -r["lots"]))[:TOP_N]
    sell = sorted([r for r in rows if r["lots"] < 0], key=lambda r: (r["amount"] is None, (r["amount"] or 0), r["lots"]))[:TOP_N]
    return {"buy": buy, "sell": sell}


def _fund_card(data: dict[str, Any], code: str, date: str) -> dict[str, Any] | None:
    m = data["meta"].get((code, date))
    if not m:
        return None
    prev = _fund_prev(data, code, date)
    pm = data["meta"].get((code, prev)) if prev else None
    name, issuer = ETF_META.get(code, (m.get("name") or code, m.get("issuer") or ""))
    units, nav = m.get("units"), m.get("nav")
    units_delta = (units - pm["units"]) if (pm and units is not None and pm.get("units") is not None) else None
    return {
        "code": code, "name": m.get("name") or name, "issuer": m.get("issuer") or issuer, "date": date, "prev": prev,
        "source": m.get("source"), "fallback": m.get("source") == FALLBACK_SOURCE,
        "nav": nav, "unitNav": round(nav / units, 2) if nav and units else None, "units": units, "unitsDelta": units_delta,
        "holdings": len(_hold(data, code, date)),
    }


def _tide(data: dict[str, Any], days: list[str], closes: dict[str, float]) -> dict[str, Any]:
    """每天五檔合計的增減金額（用最新收盤價換算，億），累計＝這幾天加起來。"""
    daily: dict[str, list[float]] = {}
    for k, day in enumerate(days):
        for code, _n, _i in ETFS:
            if day not in data["fundDates"].get(code, []):
                continue
            prev = _fund_prev(data, code, day)
            if not prev:
                continue
            for stock, (delta, _now, _before) in _deltas(_hold(data, code, day), _hold(data, code, prev)).items():
                if stock not in closes:
                    continue
                daily.setdefault(stock, [0.0] * len(days))[k] += delta * closes[stock] / 1e8
    stocks = []
    for stock, vals in daily.items():
        cum, run = [], 0.0
        for v in vals:
            run += v
            cum.append(round(run, 2))
        if max(abs(x) for x in cum) < 0.05 and max(abs(v) for v in vals) < 0.05:
            continue
        stocks.append({"code": stock, "name": _name(data, stock)[1], "daily": [round(v, 2) for v in vals], "cum": cum})
    stocks.sort(key=lambda s: -max(abs(x) for x in s["cum"] + s["daily"]))
    return {"days": days, "stocks": stocks[:TIDE_MAX_STOCKS]}


def overview(as_of: str | None = None) -> dict[str, Any]:
    data = _load()
    date = _resolve_date(data, as_of)
    if not date:
        return {"status": "missing", "reason": "還沒有主動式 ETF 的持股資料（排程主機每個交易日晚上抓各投信公告）", "dates": []}
    closes, close_date = _closes(data, date)
    funds = [c for c in (_fund_card(data, code, date) for code, _n, _i in ETFS) if c]
    today = {code: _deltas(_hold(data, code, date), _hold(data, code, _fund_prev(data, code, date)))
             for code, _n, _i in ETFS if (code, date) in data["meta"] and _fund_prev(data, code, date)}
    # 一週：往回 5 個資料日，只比頭尾兩天
    have = [d for d in data["dates"] if d <= date]
    wfrom = have[min(WEEK_SPAN, len(have) - 1)] if len(have) > 1 else None
    week: dict[str, dict[str, tuple[int, int, int]]] = {}
    if wfrom:
        for code, _n, _i in ETFS:
            head = _fund_on(data, code, wfrom)
            tail = _fund_on(data, code, date)
            if head and tail and head < tail:
                week[code] = _deltas(_hold(data, code, tail), _hold(data, code, head))
    # 最多人共同持有
    common: dict[str, dict[str, Any]] = {}
    for f in funds:
        for stock, h in _hold(data, f["code"], date).items():
            if h["shares"] <= 0:
                continue
            c = common.setdefault(stock, {"code": stock, "shares": 0, "funds": []})
            c["shares"] += h["shares"]
            c["funds"].append({"c": f["code"], "rate": round(h["weight"] or 0, 2)})
    common_rows = []
    for stock, c in common.items():
        group, name = _name(data, stock)
        amt = _yi(c["shares"], closes.get(stock))
        common_rows.append({"code": stock, "name": name, "group": group, "n": len(c["funds"]), "lots": round(c["shares"] / 1000),
                            "amount": round(amt, 1) if amt is not None else None, "funds": sorted(c["funds"], key=lambda x: -x["rate"])})
    common_rows.sort(key=lambda r: (-r["n"], -(r["amount"] or 0), r["code"]))
    tide_days = sorted(have[:TIDE_DAYS])
    t = _combine(today, closes, data)
    w = _combine(week, closes, data)
    return {
        "status": "ok", "date": date, "dates": data["dates"][:60], "closeDate": close_date,
        "nFunds": len(funds), "missing": [code for code, _n, _i in ETFS if code not in {f["code"] for f in funds}],
        "funds": funds, "totalNav": round(sum(f["nav"] or 0 for f in funds) / 1e8),
        "buy": t["buy"], "sell": t["sell"], "todayFunds": len(today),
        "wfrom": wfrom, "wto": date, "wbuy": w["buy"], "wsell": w["sell"],
        "common": common_rows[:COMMON_N], "fullCount": sum(1 for r in common_rows if r["n"] >= len(funds) and funds),
        "tide": _tide(data, tide_days, closes),
    }


# ---- 單一檔 ----

def _streaks(data: dict[str, Any], code: str, date: str) -> dict[str, list[dict[str, Any]]]:
    """同方向連 STREAK_MIN 次以上、且最新一份還在進行；沒動的日子不算中斷（ETF 常整週不動）。"""
    ds = [d for d in data["fundDates"].get(code, []) if d <= date]
    moves: dict[str, list[tuple[str, int]]] = {}
    for i in range(1, len(ds)):
        prev = _fund_prev(data, code, ds[i])
        if not prev:
            continue
        for stock, (delta, _now, _before) in _deltas(_hold(data, code, ds[i]), _hold(data, code, prev)).items():
            moves.setdefault(stock, []).append((ds[i], delta))
    cur = _hold(data, code, date)
    out: dict[str, list[dict[str, Any]]] = {"up": [], "dn": []}
    for stock, seq in moves.items():
        if not seq or seq[-1][0] != date:
            continue
        sign = 1 if seq[-1][1] > 0 else -1
        n, total, start = 0, 0, date
        for d, delta in reversed(seq):
            if (delta > 0) != (sign > 0):
                break
            n += 1
            total += delta
            start = d
        if n < STREAK_MIN:
            continue
        group, name = _name(data, stock)
        span = sum(1 for d in ds if start <= d <= date)
        out["up" if sign > 0 else "dn"].append({"code": stock, "name": name, "group": group, "delta": total, "moves": n, "span": span,
                                                "since": start, "now": cur.get(stock, {}).get("shares", 0)})
    for side in out.values():
        side.sort(key=lambda r: -abs(r["delta"]))
    return out


def fund(code: str, as_of: str | None = None) -> dict[str, Any]:
    code = str(code or "").strip().upper()
    if code not in ETF_META:
        raise LookupError(f"不認識的 ETF：{code}")
    data = _load()
    target = _resolve_date(data, as_of)
    date = _fund_on(data, code, target) if target else None
    if not date:
        raise LookupError(f"{code} 還沒有持股資料")
    card = _fund_card(data, code, date)
    cur = _hold(data, code, date)
    diff = []
    if card["prev"]:
        for stock, (delta, now, before) in _deltas(cur, _hold(data, code, card["prev"])).items():
            group, name = _name(data, stock)
            diff.append({"code": stock, "name": name, "group": group, "delta": delta, "now": now, "prev": before, "act": _act(delta, now, before)})
    diff.sort(key=lambda r: -abs(r["delta"]))
    ds = data["fundDates"][code]
    upto = [d for d in ds if d <= date]
    ffrom = upto[-1 - WEEK_SPAN] if len(upto) > WEEK_SPAN else (upto[0] if len(upto) > 1 else None)
    flow = []
    if ffrom and ffrom < date:
        for stock, (delta, now, before) in _deltas(cur, _hold(data, code, ffrom)).items():
            group, name = _name(data, stock)
            flow.append({"code": stock, "name": name, "group": group, "delta": delta, "now": now, "prev": before, "act": _act(delta, now, before)})
    flow.sort(key=lambda r: -abs(r["delta"]))
    flow = flow[:FLOW_ROWS]
    top = []
    for stock, h in sorted(cur.items(), key=lambda kv: -(kv[1]["weight"] or 0))[:FUND_TOP_N]:
        group, name = _name(data, stock)
        top.append({"code": stock, "name": name, "group": group, "rate": round(h["weight"] or 0, 2), "share": h["shares"]})
    return {
        "status": "ok", **card, "ndays": len(ds), "span": [ds[0], ds[-1]],
        "diff": diff, "flow": {"from": ffrom, "to": date, "rows": flow}, "streak": _streaks(data, code, date), "top": top,
    }


# ---- 個股 × 全部 ETF ----

def _resolve_stock(data: dict[str, Any], q: str) -> str | None:
    q = str(q or "").strip().upper()
    if not q:
        return None
    held = {s for h in data["holdings"].values() for s in h}
    if q in held:
        return q
    for stock in sorted(held):
        if _name(data, stock)[1] == q or data["names"].get(stock) == q:
            return stock
    for stock in sorted(held):
        if q in _name(data, stock)[1]:
            return stock
    return None


def stock(q: str) -> dict[str, Any]:
    data = _load()
    code = _resolve_stock(data, q)
    if not code:
        raise LookupError(f"{str(q).strip()} 不在任何一檔主動式 ETF 的持股裡")
    group, name = _name(data, code)
    latest = data["dates"][0]
    since = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=STOCK_MONTHS * 31)).strftime("%Y-%m-%d")
    funds_out, hold, rows_by_date = [], {}, {}
    for f, fname, _issuer in ETFS:
        ds = data["fundDates"].get(f, [])
        if not ds:
            continue
        shares, cost, realized = 0, None, 0.0
        touched = False
        for i, d in enumerate(ds):
            h = _hold(data, f, d).get(code)
            now = h["shares"] if h else 0
            close = _closes(data, d)[0].get(code)
            if i == 0 or not _fund_prev(data, f, d):
                # 第一份（或中間斷太久）只當起點：成本用那天收盤估
                shares, cost = now, (close if now and close else None)
                touched = touched or bool(now)
                continue
            delta = now - shares
            if delta:
                touched = True
                if delta > 0 and close:
                    cost = ((cost or close) * shares + close * delta) / now
                elif delta < 0 and close and cost:
                    realized += (close - cost) * -delta
                if now == 0:
                    cost = None
                if d >= since:
                    row = rows_by_date.setdefault(d, {"date": d, "cells": {}, "total": 0})
                    row["cells"][f] = {"delta": delta, "now": now, "act": _act(delta, now, shares)}
                    row["total"] += delta
            shares = now
        last = ds[-1]
        h = _hold(data, f, last).get(code)
        if not touched and not h:
            continue
        close_now, close_day = _closes(data, last)[0].get(code), _closes(data, last)[1]
        c = {}
        if cost and shares and close_now:
            c = {"avg": round(cost, 2), "price": close_now, "pnl": round((close_now / cost - 1) * 100, 1)}
        if realized:
            c["realized"] = round(realized)
        funds_out.append({"code": f, "name": fname})
        hold[f] = {"share": h["shares"] if h else 0, "rate": round((h or {}).get("weight") or 0, 2), "date": last, "cost": c or None}
    rows = sorted(rows_by_date.values(), key=lambda r: r["date"], reverse=True)
    close_now, close_day = _closes(data, latest)
    return {"status": "ok", "code": code, "name": name, "group": group, "funds": funds_out, "hold": hold, "rows": rows,
            "since": since, "close": close_now.get(code), "closeDate": close_day,
            "held": sum(1 for v in hold.values() if v["share"])}
