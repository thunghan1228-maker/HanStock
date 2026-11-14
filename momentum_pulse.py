"""日動能／週動能／法人動能（2026-11-14 使用者：照券商 App「動能節奏測驗」的三個分頁，用我們自己的規則和資料做）。

App 的「動能」分數沒公開算法；拿中信金、富喬、奇鋐、聯發科、南亞的走勢圖試過，最接近的是收盤跟短天期指數平均線的乖離，
但對不到一模一樣（奇鋐的週動能就差很多），上榜條件又被 VIP 鎖住，所以使用者說「照我們自己的規則做」：

- 動能＝1.4 ×（收盤 ÷ 8 日指數平均線 − 1）×100（還原股價：分割減資＋現金股利，跟熱門趨勢概念股同一套）。
  App 上的數字大多落在 −15～+15，乘 1.4 讓我們的數字跟它差不多大。
- 日動能：強勢＝今天動能 ≥ +MIN、弱勢＝≤ −MIN；照當天成交金額排（App 也是照成交金額排）。
  每檔帶「跟前一天比」的變化、上榜天數（連續幾天都在名單上，1＝今天剛上榜）、族群動能（同族群每檔動能的平均）。
- 週動能：一樣的算法換成週K（每週最後一個交易日的收盤，這週還沒結束就用最新收盤），照這週累計成交金額排，上榜幾週。
- 法人動能（外資、投信買賣超：證交所／櫃買，見 chips_daily）四張名單：
  ① 價量創新高：收盤是 240 日最高收盤、成交量是近 20 日最大、外資＋投信今天合計買超。
  ② 投信剛買準突破：投信今天買超而且是連買第 1～2 天（再之前 5 天沒買）、收盤在 60 日最高收盤的 95%～100%（快要突破、還沒過）。
  ③ 投信動能連買：投信連買 ≥ 3 天、日動能 > 0。
  ④ 外資動能連買：外資連買 ≥ 3 天、日動能 > 0。
  法人資料用「不晚於股價資料日」最新的一天；上櫃比上市晚進來時，那天只有上市的。

盤中即時（2026-11-14 使用者：日動能、週動能要跟著現在的行情跳、名單也跟著換）：後端只有收盤後的日K，
所以每檔回「到最新一天為止」的狀態，網頁拿即時價自己重算：
  日：ema＝最新一天的 8 日指數平均線 → 盤中 ema'＝a×現價＋(1−a)×ema，動能＝1.4×(現價÷ema'−1)×100；上榜天數＝昨天為止連續幾天＋1。
  週：這週還沒結束（即時報價跟最新資料同一週）→ 用「上週為止」的週 EMA 和上榜週數、這週已累計的成交金額＋今天；
      新的一週 → 用「最新那週為止」的週 EMA 和週數。
rows 用陣列（cols 是欄位名），只回最新一天成交金額或 5 日均成交值 ≥ MIN_VAL 億的股票，名單照成交金額排、小股票本來就排不上。
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Any

import heilong_picker
from chips_daily import _COMMON_STOCK, _lots, _schema as _chips_schema, _streak
from database import get_connection, initialize_database
from trend_high import div_factor, load_dividends

K = 1.4
EMA_SPAN = 8
MOM_MIN = 6.0
TOP_N = 40
EMA_SEED = 120            # 算 EMA 用最近幾根（夠長，前面的影響小到可以不管）
MIN_VAL = 0.3             # rows 只回成交金額（億）夠的股票
ROW_COLS = ("code", "name", "group", "close", "chg", "val", "val5", "score", "emaD", "dm", "dmPrev", "dS", "dW",
            "emaWd", "emaWl", "wm", "wmDone", "wval", "wSd", "wWd", "wSl", "wWl")
INST_DAYS = 12            # 法人資料往回拿幾個交易日（連買天數最多看這麼多）
FRESH_GAP = 5             # 投信「剛買」：連買第 1～2 天，再之前這麼多天都沒買超
NEAR_LOW, NEAR_HIGH = 0.95, 1.0

_cache: dict[str, Any] = {"key": None, "payload": None}
_lock = threading.Lock()


def _r(value: float | None, digits: int = 2) -> float | None:
    if value is None or value != value:
        return None
    return round(float(value), digits)


def mom_series(closes: list[float]) -> list[float]:
    """每一根的動能（舊到新）：1.4 ×（收盤 ÷ 8 日指數平均線 − 1）×100。"""
    a = 2 / (EMA_SPAN + 1)
    out, ema = [], None
    for c in closes:
        ema = c if ema is None else a * c + (1 - a) * ema
        out.append(K * (c / ema - 1) * 100 if ema else 0.0)
    return out


def ema_last(closes: list[float]) -> float | None:
    a = 2 / (EMA_SPAN + 1)
    ema = None
    for c in closes:
        ema = c if ema is None else a * c + (1 - a) * ema
    return ema


def _streak_on(values: list[float], test) -> int:
    """最後一根往回數，連續幾根符合 test。"""
    n = 0
    for v in reversed(values):
        if not test(v):
            break
        n += 1
    return n


def _inst_window(day: str) -> tuple[str | None, dict[str, dict[str, list[int]]]]:
    """不晚於 day 最新 INST_DAYS 個有法人資料的交易日：{代號: {foreign: [新到舊, 股], trust: [...]}}。"""
    initialize_database()
    with get_connection() as connection:
        _chips_schema(connection)
        dates = [str(r[0]) for r in connection.execute(
            "SELECT DISTINCT trade_date FROM institutional_daily WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?", (day, INST_DAYS))]
        if not dates:
            return None, {}
        rows = connection.execute(
            f"SELECT trade_date, stock_code, foreign_net, trust_net FROM institutional_daily WHERE trade_date IN ({','.join('?' for _ in dates)})",
            tuple(dates)).fetchall()
    index = {d: i for i, d in enumerate(dates)}
    out: dict[str, dict[str, list[int]]] = {}
    for r in rows:
        code = str(r["stock_code"]).strip().upper()
        if not _COMMON_STOCK.match(code):
            continue
        e = out.setdefault(code, {"foreign": [0] * len(dates), "trust": [0] * len(dates)})
        e["foreign"][index[str(r["trade_date"])]] = int(r["foreign_net"] or 0)
        e["trust"][index[str(r["trade_date"])]] = int(r["trust_net"] or 0)
    return dates[0], out


def compute(panel: heilong_picker.Panel, *, dividends: dict[str, list[tuple[str, float]]] | None = None,
            inst: tuple[str | None, dict[str, dict[str, list[int]]]] | None = None) -> dict[str, Any]:
    if not panel.dates:
        return {"status": "empty", "date": None}
    last = len(panel.dates) - 1
    day = panel.dates[last]
    week_of = [date.fromisoformat(d).isocalendar()[:2] for d in panel.dates]
    dividends = dividends or {}
    inst_day, inst_map = inst if inst is not None else (None, {})
    stocks: list[dict[str, Any]] = []
    for code, s in panel.series.items():
        valid = s.valid()
        if not valid or valid[-1] != last:
            continue
        ev = dividends.get(code)
        seed = valid[-max(EMA_SEED, 260):]
        closes = [s.c[j] * div_factor(ev, panel.dates[j], day) if ev else s.c[j] for j in seed]
        if not closes or closes[-1] <= 0:
            continue
        dm = mom_series(closes[-EMA_SEED:])
        # 週K：每週最後一根
        wk_close: list[float] = []
        wk_val: list[float] = []
        cur = None
        for j, c in zip(seed, closes):
            w = week_of[j]
            val = s.c[j] * s.v[j] * 1000 / 1e8
            if w != cur:
                wk_close.append(c)
                wk_val.append(val)
                cur = w
            else:
                wk_close[-1] = c
                wk_val[-1] += val
        wm = mom_series(wk_close)
        c = s.c[last]
        win240 = closes[-240:]
        vols20 = [s.v[j] for j in valid[-20:]]
        hi60 = max(closes[-61:-1]) if len(closes) > 1 else None   # 今天以前 60 天的最高收盤
        ch = s.chg[last]
        strong, weak = (lambda v: v >= MOM_MIN), (lambda v: v <= -MOM_MIN)
        stocks.append({
            "code": code, "name": s.name, "group": s.group, "close": c, "chg": ch if ch == ch else None,
            "val": c * s.v[last] * 1000 / 1e8, "val5": s.val5[last] if s.val5[last] == s.val5[last] else None, "wval": wk_val[-1] if wk_val else None,
            "dm": dm[-1], "dmPrev": dm[-2] if len(dm) > 1 else None, "dmSeries": dm[-30:],
            "wm": wm[-1], "wmPrev": wm[-2] if len(wm) > 1 else None, "wmSeries": wm[-12:],
            "emaD": ema_last(closes[-EMA_SEED:]), "emaWl": ema_last(wk_close), "emaWd": ema_last(wk_close[:-1]) if len(wk_close) > 1 else None,
            "dS": _streak_on(dm, strong), "dW": _streak_on(dm, weak),
            "wSl": _streak_on(wm, strong), "wWl": _streak_on(wm, weak),
            "wSd": _streak_on(wm[:-1], strong), "wWd": _streak_on(wm[:-1], weak),
            "newHigh": closes[-1] >= max(win240) - 1e-9, "volHigh": s.v[last] >= max(vols20) and s.v[last] > 0,
            "nearHi60": bool(hi60) and NEAR_LOW * hi60 <= closes[-1] < NEAR_HIGH * hi60,
            "score": s.score[last] if s.score[last] >= 0 else None,
        })
    # 族群動能＝同族群（有族群的）每檔今天動能的平均
    gsum: dict[str, list[float]] = {}
    for x in stocks:
        if x["group"]:
            gsum.setdefault(x["group"], []).append(x["dm"])
    gmom = {g: sum(v) / len(v) for g, v in gsum.items()}

    def base(x: dict[str, Any]) -> dict[str, Any]:
        return {"code": x["code"], "name": x["name"], "group": x["group"], "close": _r(x["close"]), "chg": _r(x["chg"]),
                "score": x["score"], "groupMom": _r(gmom.get(x["group"]), 1)}

    def day_row(x: dict[str, Any], strong: bool) -> dict[str, Any]:
        test = (lambda v: v >= MOM_MIN) if strong else (lambda v: v <= -MOM_MIN)
        return {**base(x), "mom": _r(x["dm"], 1), "momChg": _r(x["dm"] - x["dmPrev"], 2) if x["dmPrev"] is not None else None,
                "days": _streak_on(x["dmSeries"], test), "val": _r(x["val"], 1)}

    def week_row(x: dict[str, Any], strong: bool) -> dict[str, Any]:
        test = (lambda v: v >= MOM_MIN) if strong else (lambda v: v <= -MOM_MIN)
        return {**base(x), "mom": _r(x["wm"], 1), "momChg": _r(x["wm"] - x["wmPrev"], 2) if x["wmPrev"] is not None else None,
                "weeks": _streak_on(x["wmSeries"], test), "val": _r(x["wval"], 1)}

    by_val = sorted(stocks, key=lambda x: -x["val"])
    by_wval = sorted(stocks, key=lambda x: -(x["wval"] or 0))
    day_out = {
        "strong": [day_row(x, True) for x in by_val if x["dm"] >= MOM_MIN][:TOP_N],
        "weak": [day_row(x, False) for x in by_val if x["dm"] <= -MOM_MIN][:TOP_N],
    }
    week_out = {
        "strong": [week_row(x, True) for x in by_wval if x["wm"] >= MOM_MIN][:TOP_N],
        "weak": [week_row(x, False) for x in by_wval if x["wm"] <= -MOM_MIN][:TOP_N],
    }
    lists: dict[str, list[dict[str, Any]]] = {"priceVolHigh": [], "trustFresh": [], "trustStreak": [], "foreignStreak": []}
    for x in by_val:
        e = inst_map.get(x["code"])
        if not e:
            continue
        f, t = e["foreign"], e["trust"]
        fs, ts = _streak(f), _streak(t)
        row = {**base(x), "mom": _r(x["dm"], 1), "val": _r(x["val"], 1), "f": _lots(f[0]), "t": _lots(t[0]), "fs": fs, "ts": ts,
               "f5": _lots(sum(f[:5])), "t5": _lots(sum(t[:5]))}
        if x["newHigh"] and x["volHigh"] and f[0] + t[0] > 0:
            lists["priceVolHigh"].append(row)
        if t[0] > 0 and 1 <= ts <= 2 and not any(v > 0 for v in t[ts:ts + FRESH_GAP]) and x["nearHi60"]:
            lists["trustFresh"].append(row)
        if ts >= 3 and x["dm"] > 0:
            lists["trustStreak"].append(row)
        if fs >= 3 and x["dm"] > 0:
            lists["foreignStreak"].append(row)
    for k in lists:
        lists[k] = lists[k][:TOP_N]
    def row_arr(x: dict[str, Any]) -> list[Any]:
        return [x["code"], x["name"], x["group"], _r(x["close"]), _r(x["chg"]), _r(x["val"], 2), _r(x["val5"], 2), x["score"],
                _r(x["emaD"], 4), _r(x["dm"], 2), _r(x["dmPrev"], 2), x["dS"], x["dW"],
                _r(x["emaWd"], 4), _r(x["emaWl"], 4), _r(x["wm"], 2), _r(x["wmPrev"], 2), _r(x["wval"], 2), x["wSd"], x["wWd"], x["wSl"], x["wWl"]]

    rows = [row_arr(x) for x in by_val if max(x["val"], x["val5"] or 0) >= MIN_VAL]
    iso = date.fromisoformat(day).isocalendar()
    return {
        "status": "ok", "date": day, "week": f"{iso[0]}-W{iso[1]:02d}", "rule": {"k": K, "ema": EMA_SPAN, "min": MOM_MIN, "top": TOP_N},
        "day": day_out, "weekly": week_out, "inst": {"date": inst_day, "lists": lists},
        "universe": len(stocks), "cols": list(ROW_COLS), "rows": rows,
    }


def payload() -> dict[str, Any]:
    panel = heilong_picker.load_panel()
    inst = _inst_window(panel.dates[-1]) if panel.dates else (None, {})
    key = (panel.key, inst[0], sum(len(v["foreign"]) for v in inst[1].values()))
    with _lock:
        if _cache["key"] == key and _cache["payload"] is not None:
            return _cache["payload"]
    out = compute(panel, dividends=load_dividends(), inst=inst)
    out["loadedAt"] = panel.loaded_at
    with _lock:
        _cache["key"], _cache["payload"] = key, out
    return out
