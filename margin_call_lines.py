"""融資斷頭線（2026-10-23 使用者貼老師 10/22 收盤「台股融資追蹤・斷頭線就在腳下」圖文）。

融資維持率 = 股票市值 ÷ 融資借款；借滿六成的人，股價跌 22% 維持率剛好 130%（收追繳令、2 天沒補錢第 3 天強制賣出＝斷頭）。
老師用兩種算法抓「追高的人」的斷頭價：
  A 最高點追 ＝ 近一個月（20 個交易日）盤中最高價 × 0.78（上櫃 × 0.65）：最慘的那批人，A 跌破＝斷頭開始；
  B 五日線追 ＝ 月高那天的 5 日均價（收盤）× 0.78（上櫃 × 0.65）：比較多人的平均成本，B 跌破＝斷頭高峰。
今收低於斷頭價＝已破；否則「差 x%」＝（今收 − 斷頭價）÷ 今收。名單照 B 的距離由近到遠排。
老師 10/22 的八檔（南亞科、國巨、聯電、立隆電、聯發科、鈺邦、環球晶、凱美）月高、五日線、A、B、差幾 % 都用日K算得出一模一樣。
老師挑的「23 檔熱門股」怎麼挑的看不出來（不是單純的族群，他還有聯電、聯發科），這裡預設用被動元件、記憶體、矽晶圓三個族群，
也可以指定族群、或給代號。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

FACTOR_TSE = 0.78            # 上市：借 6 成、跌 22% 剛好 130%
FACTOR_OTC = 0.65            # 上櫃（老師：環球晶 1260 × 0.65 = 819）
HIGH_DAYS = 20               # 「近一個月」：20 個交易日（老師的聯電、聯發科月高在 9/22，剛好是 10/22 往前第 20 個交易日）
MA_DAYS = 5
NEAR_LIMIT_PCT = 10.0        # 「只差一根跌停」：B 線離今收 ≤10%
DEFAULT_GROUPS = ("被動元件", "記憶體", "矽晶圓")
LOOKBACK_CALENDAR_DAYS = 70


def factor_of(market: str | None) -> float:
    return FACTOR_OTC if str(market or "").upper() in ("OTC", "TPEX", "上櫃") else FACTOR_TSE


def lines_for(bars: list[tuple[str, float, float]], market: str | None = None) -> dict[str, Any] | None:
    """bars 舊到新（日期, 最高, 收盤），最後一根是今天。日K不到 HIGH_DAYS + MA_DAYS − 1 根（月高那天前面要有 4 根算五日線）就回 None。"""
    if len(bars) < HIGH_DAYS + MA_DAYS - 1:
        return None
    window = bars[-HIGH_DAYS:]
    high_i = max(range(len(window)), key=lambda i: (window[i][1], i))   # 同價取最近的一天
    idx = len(bars) - HIGH_DAYS + high_i
    if idx < MA_DAYS - 1:
        return None
    month_high = bars[idx][1]
    ma5 = sum(b[2] for b in bars[idx - MA_DAYS + 1:idx + 1]) / MA_DAYS
    close = bars[-1][2]
    k = factor_of(market)
    a, b = month_high * k, ma5 * k
    gap = lambda line: (close - line) / close * 100 if close > 0 else None  # noqa: E731
    return {
        "market": "OTC" if k == FACTOR_OTC else "TSE", "factor": k, "close": close, "date": bars[-1][0],
        "monthHigh": month_high, "highDate": bars[idx][0], "ma5": round(ma5, 2),
        "a": round(a, 2), "b": round(b, 2), "aGap": round(gap(a), 2), "bGap": round(gap(b), 2),
        "aBroken": close < a, "bBroken": close < b,
    }


def _group_members(groups: list[str]) -> dict[str, list[str]]:
    from stock_groups import STOCK_GROUPS

    return {g: [str(c).strip().upper() for c, _n in STOCK_GROUPS.get(g, [])] for g in groups}


def payload(as_of: str | None = None, groups: list[str] | None = None, codes: list[str] | None = None, limit: int = 60) -> dict[str, Any]:
    """名單：指定族群（沒給用預設三個、給 all 就是全部族群）加上額外代號，照 B 的距離排（已破的在最前面）。"""
    from brew_launch import _latest_bar_date, _load_bars
    from brew_launch_history import _markets, group_and_name
    from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

    as_of = as_of or _latest_bar_date()
    if not as_of:
        return {"status": "empty", "date": None, "reason": "還沒有日K資料"}
    if groups and "all" in groups:
        names = [g for g in STOCK_GROUPS if g not in SPECIAL_GROUP_NAMES]
    else:
        names = [g for g in (groups or list(DEFAULT_GROUPS)) if g in STOCK_GROUPS]
    members = _group_members(names)
    code_group: dict[str, str] = {}
    for g, cs in members.items():
        for c in cs:
            code_group.setdefault(c, g)
    for c in codes or []:
        code_group.setdefault(str(c).strip().upper(), group_and_name(str(c).strip().upper())[0] or "")
    wanted = sorted(code_group)
    session = (datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    bars_by_code = _load_bars(wanted, session=session)
    markets = _markets(wanted)
    rows: list[dict[str, Any]] = []
    for code in wanted:
        bars = [(b[0], b[1], b[3]) for b in bars_by_code.get(code, []) if b[0] <= as_of]
        if not bars or bars[-1][0] != as_of:
            continue
        info = lines_for(bars, markets.get(code))
        if not info:
            continue
        group, name = group_and_name(code)
        rows.append({"code": code, "name": name, "group": code_group.get(code) or group, **info})
    rows.sort(key=lambda r: (r["bGap"], r["aGap"], r["code"]))
    counts = {"stocks": len(rows), "aBroken": sum(1 for r in rows if r["aBroken"]), "bBroken": sum(1 for r in rows if r["bBroken"]),
              "bNear": sum(1 for r in rows if not r["bBroken"] and r["bGap"] <= NEAR_LIMIT_PCT)}
    return {"status": "ok", "date": as_of, "groups": names, "counts": counts, "rows": rows[:max(1, min(limit, 400))],
            "rule": {"factorTse": FACTOR_TSE, "factorOtc": FACTOR_OTC, "highDays": HIGH_DAYS, "maDays": MA_DAYS, "nearPct": NEAR_LIMIT_PCT}}
