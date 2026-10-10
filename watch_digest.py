"""自選股一頁看完（2026-10-10 使用者第 3 項之 4）：每檔自選股的內部人動向、下次法說、近四季 EPS／本益比放同一張表。

- 內部人：最近三個月董監＋經理人淨增減（張，已扣配股／減資）、最新月集中市場買賣、集保 400 張大戶近 4 週變化（insider_watch）。
- 法說：公開資訊觀測站法人說明會一覽表裡，還沒結束的最近一場（macro_calendar 存的鏡像；分自辦與受邀券商論壇）。
- 季報：近四季 EPS 合計、最新一季 EPS 與年增、營收年增、本益比（stock_profile 的快取，3 天內不重抓）。
  沒快取的季報每次最多補抓 FIN_BUDGET 檔，其他放在 pending，前端過幾秒再要一次就會補齊。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Callable

from database import get_connection, initialize_database

MAX_CODES = 120
FIN_BUDGET = 6
INSIDER_MONTHS = 3
CODE_RE = re.compile(r"[0-9A-Z]{4,6}")


def _codes(raw: Any) -> list[str]:
    items = raw.split(",") if isinstance(raw, str) else list(raw or [])
    out: list[str] = []
    for c in items:
        c = str(c).strip().upper()
        if CODE_RE.fullmatch(c) and c not in out:
            out.append(c)
    return out[:MAX_CODES]


def _conferences() -> list[dict[str, Any]]:
    import macro_calendar

    initialize_database()
    with get_connection() as connection:
        macro_calendar._schema(connection)
        row = connection.execute("SELECT value FROM macro_calendar WHERE key = 'conference'").fetchone()
    return json.loads(row["value"]) if row else []


def next_calls(codes: list[str], today: str) -> dict[str, dict[str, Any]]:
    """每檔還沒結束的最近一場法說（開始日 ≥ 今天，或區間還沒過完）。"""
    from macro_calendar import INVITED_RE

    want = set(codes)
    out: dict[str, dict[str, Any]] = {}
    for r in sorted(_conferences(), key=lambda x: (x.get("start") or "", x.get("time") or "")):
        code = str(r.get("code") or "")
        if code not in want or code in out or (r.get("end") or r.get("start") or "") < today:
            continue
        summary = r.get("summary") or ""
        out[code] = {"date": r["start"], "until": r.get("end") if r.get("end") and r.get("end") != r["start"] else None,
                     "time": r.get("time") or "", "invited": bool(INVITED_RE.search(summary)), "place": (r.get("place") or "")[:60],
                     "summary": summary[:100]}
    return out


def insider_rows(codes: list[str]) -> tuple[str | None, dict[str, dict[str, Any]]]:
    import insider_watch as iw

    months = iw.load_months()[:INSIDER_MONTHS]
    if not months:
        return None, {}
    latest = months[0]
    prices = iw.avg_close(codes, latest)
    out: dict[str, dict[str, Any]] = {}
    for i, month in enumerate(months):
        data = iw._load_month(month)
        if not data:
            continue
        prev = (iw._load_month(iw._prev_month(month)) or {}).get("byCode", {})
        for code in codes:
            r = data["byCode"].get(code)
            if not r:
                continue
            row = iw._row(r, prev.get(code), data["detail"].get(code), prices.get(code) if i == 0 else None)
            item = out.setdefault(code, {"name": r.get("name"), "months": []})
            item["months"].append({"month": month, "netLots": row["netLots"], "dirPct": row["dirPct"]})
            if i == 0:
                c = row.get("central")
                item.update({"month": month, "netLots": row["netLots"], "amount": row["amount"], "dirPct": row["dirPct"],
                             "netPctOfIssued": row["netPctOfIssued"],
                             "centralLots": round(c["net"] / 1000) if c else None, "centralAmount": c["amount"] if c else None,
                             "detail": bool(c)})
    big = iw.big_holders(codes)
    for code, b in big.items():
        out.setdefault(code, {"months": []})["big400"] = {"date": b["date"], "pct": b["pct"], "chg4w": b["chg4w"]}
    for code, item in out.items():
        lots = [m["netLots"] for m in item["months"]]
        item["streak"] = 0
        for v in lots:                       # 最近連續幾個月同方向（正＝連增、負＝連減）
            if v == 0 or (item["streak"] and (v > 0) != (item["streak"] > 0)):
                break
            item["streak"] += 1 if v > 0 else -1
        chg = (item.get("big400") or {}).get("chg4w")
        net = item.get("netLots")
        item["combo"] = ("雙買" if net and net > 0 and chg is not None and chg > 0 else
                         "雙賣" if net and net < 0 and chg is not None and chg < 0 else None)
    return latest, out


def fin_rows(codes: list[str], *, now: datetime, budget: int, fetcher: Callable[[dict[str, str]], Any] | None = None
             ) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, str]]:
    """季報摘要：快取有就用；沒有（或過期）且還有額度就抓；額度用完的放 pending。"""
    import stock_profile as sp

    call = fetcher or sp._default_fetcher
    start = f"{now.year - 3}-01-01"
    out: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    errors: dict[str, str] = {}
    for code in codes:
        key = f"fin:{code}"
        hit = sp._cache_get(key, sp.FIN_TTL_DAYS, now)
        stale = isinstance(hit, dict) and "__stale__" in hit
        fin = hit["__stale__"] if stale else hit
        if hit is None or stale:
            if budget > 0:
                budget -= 1
                fin, err = sp._cached(key, sp.FIN_TTL_DAYS,
                                      lambda c=code: sp.parse_financials(call({"dataset": "TaiwanStockFinancialStatements", "data_id": c,
                                                                               "start_date": start})), now)
                if err:
                    errors[code] = err
            elif hit is None:
                pending.append(code)
                continue
        quarters = fin or []
        last4 = quarters[:4]
        ttm = round(sum(q["eps"] for q in last4), 2) if len(last4) == 4 and all(q["eps"] is not None for q in last4) else None
        close_date, close = sp._latest_close(code)
        q0 = quarters[0] if quarters else {}
        out[code] = {"ttmEps": ttm, "close": close, "closeDate": close_date, "pe": round(close / ttm, 1) if close and ttm and ttm > 0 else None,
                     "quarter": q0.get("label"), "eps": q0.get("eps"), "epsYoY": q0.get("epsYoY"), "epsTurn": q0.get("epsTurn"),
                     "revYoY": q0.get("revYoY"), "netMargin": q0.get("net"), "quarters": len(quarters)}
    return out, pending, errors


def digest(raw_codes: Any, *, now: datetime | None = None, budget: int = FIN_BUDGET,
           fetcher: Callable[[dict[str, str]], Any] | None = None) -> dict[str, Any]:
    import stock_profile as sp

    now = now or sp._now()
    codes = _codes(raw_codes)
    if not codes:
        return {"status": "empty", "rows": [], "pending": []}
    month, insiders = insider_rows(codes)
    calls = next_calls(codes, now.date().isoformat())
    fins, pending, errors = fin_rows(codes, now=now, budget=budget, fetcher=fetcher)
    info = sp._cache_get("info", sp.INFO_TTL_DAYS, now) or {}       # 只看快取（個股研究打開過就有），不為了股名多抓
    info = info.get("__stale__", info) if isinstance(info, dict) else {}
    conf_names = {str(c.get("code")): c.get("name") for c in _conferences() if c.get("name")}
    rows = []
    for code in codes:
        ins = insiders.get(code)
        name = (info.get(code) or {}).get("name") or conf_names.get(code) or (ins or {}).get("name")
        rows.append({"code": code, "name": name, "insider": ins, "call": calls.get(code), "fin": fins.get(code), "finError": errors.get(code)})
    return {
        "status": "ok", "now": now.isoformat(timespec="seconds"), "insiderMonth": month, "rows": rows, "pending": pending,
        "rule": ("內部人＝董監＋經理人本月淨增減（張，已扣配股／減資的同比例變動），後面是前兩個月；集中市場＝真的在市場上買賣的部分（有查明細的才有）。"
                 "大戶＝集保 400 張以上持股比近 4 週變化（百分點）；內部人買＋大戶增＝雙買。下次法說＝觀測站已公告、還沒結束的最近一場，"
                 "📢 公司自辦（多半是公布季報）、其他是受邀券商論壇。近四季 EPS＝最近四季 EPS 加總；本益比＝現價 ÷ 近四季 EPS。"),
        "source": "公開資訊觀測站（內部人持股異動、法人說明會一覽表、財報）、集保結算所股權分散表；季報經 FinMind 公開資料",
    }
