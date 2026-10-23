"""融資斷頭線（2026-10-23 使用者貼老師 10/22 收盤「台股融資追蹤・斷頭線就在腳下」圖文）。

融資維持率 = 股票市值 ÷ 融資借款；借滿六成的人，股價跌 22% 維持率剛好 130%（收追繳令、2 天沒補錢第 3 天強制賣出＝斷頭）。
老師用兩種算法抓「追高的人」的斷頭價：
  A 最高點追 ＝ 近一個月（20 個交易日）盤中最高價 × 0.78（上櫃 × 0.65）：最慘的那批人，A 跌破＝斷頭開始；
  B 五日線追 ＝ 月高那天的 5 日均價（收盤）× 0.78（上櫃 × 0.65）：比較多人的平均成本，B 跌破＝斷頭高峰。
今收低於斷頭價＝已破；否則「差 x%」＝（今收 − 斷頭價）÷ 今收。名單照 B 的距離由近到遠排。
再加每檔的融資餘額（margin_balance.py，證交所／櫃買每天整張表）：只看價格不知道那個價位附近還有沒有融資，
所以名單每檔多給融資餘額（張／億）、使用率、今天增減、五日增減、月高那天到今天增減（負的＝融資在退場）。
老師最後一頁的總結：「A 線已破」＝今收跌破 A 的檔數與名單、「B 線 5% 內」＝B 還沒破但離今收 ≤5% 的檔數與名單
（10/22：A 已破南亞科、國巨、聯電、聯發科、天二科技；B 5% 內南亞科、國巨）。天二科技是上市股（×0.78，A 131.04、今收 130 已破）。
老師 10/22 的八檔（南亞科、國巨、聯電、立隆電、聯發科、鈺邦、環球晶、凱美）月高、五日線、A、B、差幾 % 都用日K算得出一模一樣。
老師挑的「23 檔熱門股」怎麼挑的看不出來（不是單純的族群，他還有聯電、聯發科），這裡預設用被動元件、記憶體、矽晶圓三個族群，
也可以指定族群、或給代號。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from margin_balance import metrics as margin_metrics

logger = logging.getLogger(__name__)

FACTOR_TSE = 0.78            # 上市：借 6 成、跌 22% 剛好 130%
FACTOR_OTC = 0.65            # 上櫃（老師：環球晶 1260 × 0.65 = 819）
HIGH_DAYS = 20               # 「近一個月」：20 個交易日（老師的聯電、聯發科月高在 9/22，剛好是 10/22 往前第 20 個交易日）
MA_DAYS = 5
NEAR_LIMIT_PCT = 10.0        # 「只差一根跌停」：B 線離今收 ≤10%
SUMMARY_NEAR_PCT = 5.0       # 老師總結「B 線 5% 內」：10/22 聯電 B 差 5.2% 不算、南亞科 1.0%／國巨 3.8% 算
DEFAULT_GROUPS = ("被動元件", "記憶體", "矽晶圓")
LOOKBACK_CALENDAR_DAYS = 70
BURST_RATIO = 2.0            # 爆量：近 5 日內有一天成交量 ≥ 前 20 日均量的 2 倍（使用者 2026-10-23：爆大量、跌幅變小大概是落底）
BURST_DAYS = 5
AVG_VOL_DAYS = 20


def stabilize(bars: list[tuple[str, float, float, float, float]]) -> dict[str, Any] | None:
    """止跌特徵（給個股問診的「近似落底」用）。bars 舊到新（日期, 最高, 最低, 收盤, 成交量張），最後一根是資料日。
    burst＝近 BURST_DAYS 日內量最大的那天 ≥ 它前 AVG_VOL_DAYS 日均量的 BURST_RATIO 倍（爆量）；
    shrinking＝昨天收跌、今天漲跌幅比昨天好（跌幅縮小或轉漲）；noNewLow＝今天最低沒有低於爆量那天的最低（爆量之後沒再破低）。
    日K不夠（要有爆量日前 20 日的量）回 None。"""
    n = len(bars)
    if n < AVG_VOL_DAYS + BURST_DAYS + 1:
        return None
    best: tuple[float, int] | None = None
    for j in range(n - BURST_DAYS, n):
        base = sum(b[4] for b in bars[j - AVG_VOL_DAYS:j]) / AVG_VOL_DAYS
        if base > 0:
            ratio = bars[j][4] / base
            if best is None or ratio >= best[0]:
                best = (ratio, j)
    if best is None or bars[-2][3] <= 0 or bars[-3][3] <= 0:
        return None
    ratio, j = best
    chg = (bars[-1][3] / bars[-2][3] - 1) * 100
    chg_prev = (bars[-2][3] / bars[-3][3] - 1) * 100
    return {"burst": ratio >= BURST_RATIO, "burstRatio": round(ratio, 1), "burstDate": bars[j][0],
            "chg": round(chg, 2), "chgPrev": round(chg_prev, 2),
            "shrinking": chg_prev < 0 and chg > chg_prev, "noNewLow": bars[-1][2] >= bars[j][2]}


PULL_RUN_RET10 = 0.25        # 漲多：近 10 日漲 ≥25%，或離 20 日均線乖離 ≥20%
PULL_RUN_BIAS20 = 0.20
PULL_EXTREME_RET10 = 0.40    # 極端漲多：近 10 日漲 ≥40%（回測近一年這組短線回檔機率最高）
PULL_BURST = 2.0             # 爆量：今天量 ≥ 前 20 日均量 2 倍


def pullback(bars: list[tuple[str, float, float, float, float]], open_today: float | None) -> dict[str, Any] | None:
    """短線回檔警示的價量特徵（使用者 2026-10-23 要「近似高點」；回測近一年 417 檔顯示價量條件抓不到頭部，只對 1～5 天的短線回檔有一點傾向，所以名稱用「留意回檔」）。
    bars 舊到新（日期, 最高, 最低, 收盤, 成交量張），最後一根是資料日；open_today＝那天開盤價（黑K、長上影要用）。
    tier 2＝近 10 日漲 ≥40% ＋ 爆量 ＋ 出貨K；tier 1＝漲多（近 10 日 ≥25% 或乖離月線 ≥20%）＋ 爆量 ＋ 出貨K。
    出貨K＝收黑、長上影（上影 ≥ 實體 2 倍且 ≥2.5%）、或爆量不漲（漲幅 <1%）。日K不夠（要 31 根）或沒開盤價回 None。"""
    n = len(bars)
    if n < 31 or not open_today or open_today <= 0:
        return None
    _, high, _low, close, vol = bars[-1]
    prev_close = bars[-2][3]
    base = sum(b[4] for b in bars[-21:-1]) / 20
    if base <= 0 or prev_close <= 0 or close <= 0:
        return None
    ma20 = sum(b[3] for b in bars[-20:]) / 20
    ret10 = close / bars[-11][3] - 1
    bias20 = close / ma20 - 1
    vr = vol / base
    chg = close / prev_close - 1
    body, upper = abs(close - open_today), high - max(open_today, close)
    black = close < open_today
    shadow = upper >= 2 * max(body, 0.002 * close) and upper / close >= 0.025
    run = ret10 >= PULL_RUN_RET10 or bias20 >= PULL_RUN_BIAS20
    burst = vr >= PULL_BURST
    dump = black or shadow or chg < 0.01
    tier = 2 if (ret10 >= PULL_EXTREME_RET10 and burst and dump) else 1 if (run and burst and dump) else 0
    return {"tier": tier, "ret10": round(ret10 * 100, 1), "bias20": round(bias20 * 100, 1), "vr": round(vr, 1), "chg": round(chg * 100, 2),
            "black": black, "shadow": shadow, "run": run, "burst": burst, "dump": dump}


def live_base(full: list[tuple[str, float, float, float, float]]) -> dict[str, Any] | None:
    """盤中版落底／回檔警示要用的「昨天以前」基底（2026-10-23 使用者：落底訊號、回檔警示盤中有發生要跳通知）。
    full＝舊到新（日期, 最高, 最低, 收盤, 成交量張），最後一根是最近一個收盤日；前端拿「今天到目前的 開高低現價＋累計量」當今天那根暫時的日K，
    接在這些後面，用跟 stabilize／pullback 一模一樣的算法重算。這裡預先算好需要的幾個數字（前端不必拿整串日K）：
      prevClose＝昨收、prevChg＝昨天漲跌幅 %、c10＝往前第 10 個交易日收盤（算近 10 日漲幅）、sum19＝最近 19 日收盤合計（加上現價就是 20 日均線）、
      avgVol20＝最近 20 日均量（爆量的基準）、burstPrev＝今天以前的近 BURST_DAYS−1 天裡量比最大的那天 {ratio, low, date}（今天的量比拿去跟它比）。
    日K不夠（要 30 根；落底的爆量日要 20＋BURST_DAYS 根）的欄位是 None。"""
    n = len(full)
    if n < 10:
        return None
    out: dict[str, Any] = {"prevClose": full[-1][3], "c10": full[-10][3], "sum19": None, "avgVol20": None, "prevChg": None, "burstPrev": None}
    if n >= 19:
        out["sum19"] = sum(b[3] for b in full[-19:])
    if n >= AVG_VOL_DAYS:
        out["avgVol20"] = sum(b[4] for b in full[-AVG_VOL_DAYS:]) / AVG_VOL_DAYS
    if n >= 2 and full[-2][3] > 0 and full[-1][3] > 0:
        out["prevChg"] = (full[-1][3] / full[-2][3] - 1) * 100
    if n >= AVG_VOL_DAYS + BURST_DAYS:
        best: tuple[float, int] | None = None
        for j in range(n + 1 - BURST_DAYS, n):          # 今天（第 n 根）以前的那幾天
            base = sum(b[4] for b in full[j - AVG_VOL_DAYS:j]) / AVG_VOL_DAYS
            if base > 0:
                ratio = full[j][4] / base
                if best is None or ratio >= best[0]:
                    best = (ratio, j)
        if best is not None:
            out["burstPrev"] = {"ratio": best[0], "low": full[best[1]][2], "date": full[best[1]][0]}
    return out


def _load_open_map(codes: list[str], as_of: str) -> dict[str, float]:
    """{代號: as_of 那天的開盤價}（_load_bars 沒帶開盤價，黑K／長上影要用）。"""
    from database import get_connection, initialize_database

    initialize_database()
    out: dict[str, float] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"SELECT stock_code, open FROM bars_1d WHERE substr(bar_time, 1, 10) = ? AND stock_code IN ({','.join('?' for _ in batch)})",
                (as_of, *batch),
            ).fetchall()
            for row in rows:
                if row["open"]:
                    out[str(row["stock_code"]).strip().upper()] = float(row["open"])
    return out


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
    prev_close = bars[-2][2]
    k = factor_of(market)
    a, b = month_high * k, ma5 * k
    gap = lambda line: (close - line) / close * 100 if close > 0 else None  # noqa: E731
    return {
        "market": "OTC" if k == FACTOR_OTC else "TSE", "factor": k, "close": close, "date": bars[-1][0],
        "monthHigh": month_high, "highDate": bars[idx][0], "ma5": round(ma5, 2),
        "a": round(a, 2), "b": round(b, 2), "aGap": round(gap(a), 2), "bGap": round(gap(b), 2),
        "aBroken": close < a, "bBroken": close < b,
        "aNew": close < a <= prev_close, "bNew": close < b <= prev_close,   # 今天剛跌破：昨收還在線上、今收在線下
    }


def single(code: str, as_of: str | None = None) -> dict[str, Any]:
    """單檔的斷頭價 A／B（K線圖日線畫線、5 分K 離線近才畫用），算法同 lines_for；沒有日K／日K不夠就回 status 說明原因。"""
    from brew_launch import _latest_bar_date, _load_bars
    from brew_launch_history import _markets

    code = str(code or "").strip().upper()
    as_of = as_of or _latest_bar_date()
    if not code or not as_of:
        return {"status": "empty", "code": code, "date": as_of, "reason": "沒有股號或日K"}
    session = (datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    full = [b for b in _load_bars([code], session=session).get(code, []) if b[0] <= as_of]
    bars = [(b[0], b[1], b[3]) for b in full]
    if not bars:
        return {"status": "missing", "code": code, "date": as_of, "reason": "本站沒有這檔的日K"}
    info = lines_for(bars, _markets([code]).get(code))
    if not info:
        return {"status": "short", "code": code, "date": bars[-1][0], "reason": "日K不足 24 根，算不出斷頭線"}
    return {"status": "ok", "code": code, "date": info["date"], "line": {**info, "stab": stabilize(full), "pull": pullback(full, _load_open_map([code], as_of).get(code))}}


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
    opens = _load_open_map(wanted, as_of)
    rows: list[dict[str, Any]] = []
    margin_hist: dict[str, dict[str, dict[str, int]]] = {}
    try:
        from margin_balance import history as margin_history

        margin_hist = margin_history(wanted, (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d"), as_of)
    except Exception:  # noqa: BLE001
        logger.exception("融資餘額讀取失敗")
    for code in wanted:
        full = [b for b in bars_by_code.get(code, []) if b[0] <= as_of]
        bars = [(b[0], b[1], b[3]) for b in full]
        if not bars or bars[-1][0] != as_of:
            continue
        info = lines_for(bars, markets.get(code))
        if not info:
            continue
        group, name = group_and_name(code)
        rows.append({"code": code, "name": name, "group": code_group.get(code) or group, **info, "stab": stabilize([(b[0], b[1], b[2], b[3], b[4]) for b in full]), "pull": pullback([(b[0], b[1], b[2], b[3], b[4]) for b in full], opens.get(code)),
                     "liveBase": live_base([(b[0], b[1], b[2], b[3], b[4]) for b in full]),
                     "margin": margin_metrics(margin_hist.get(code), as_of, info["highDate"], info["close"])})
    rows.sort(key=lambda r: (r["bGap"], r["aGap"], r["code"]))
    counts = {"stocks": len(rows), "aBroken": sum(1 for r in rows if r["aBroken"]), "bBroken": sum(1 for r in rows if r["bBroken"]),
              "bNear": sum(1 for r in rows if not r["bBroken"] and r["bGap"] <= NEAR_LIMIT_PCT)}
    pick = lambda hit: [{"code": r["code"], "name": r["name"]} for r in rows if hit(r)]  # noqa: E731
    summary = {"aBroken": pick(lambda r: r["aBroken"]), "bWithin": pick(lambda r: not r["bBroken"] and r["bGap"] <= SUMMARY_NEAR_PCT),
               "aNew": pick(lambda r: r["aNew"]), "bNew": pick(lambda r: r["bNew"])}
    return {"status": "ok", "date": as_of, "groups": names, "counts": counts, "summary": summary, "rows": rows[:max(1, min(limit, 1000))],
            "rule": {"factorTse": FACTOR_TSE, "factorOtc": FACTOR_OTC, "highDays": HIGH_DAYS, "maDays": MA_DAYS, "nearPct": NEAR_LIMIT_PCT,
                     "summaryNearPct": SUMMARY_NEAR_PCT}}
