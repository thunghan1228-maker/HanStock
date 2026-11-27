"""選股模型：只看籌碼的五個模型，各種調整前後的回測比較。

2026-11-27 使用者：「調只看籌碼那幾個模型的條件」，照建議做：
大戶法人同步、千張大戶連增多頭、大戶進散戶退、大戶吸籌當沖熱、大戶法人合買點火這五個，
一年回測都是賠的（同期大盤 +43%）。看到的問題：
- 集保一週公布一次，公布後那天一次冒出幾十檔（大戶法人同步最多同時抱 170 檔），等於沒在挑。
- 條件只要「站上月線」，很多貼著月線買，最近 20 筆有 11 筆買進當天就跌破月線賣掉。
- 沒等股價表態：唯一明顯賺的營收起飛爆量有「帶量上漲、創 20 日新高」。

這裡把每個調整單獨加上去、以及全部套用，跟原版並排比。為了不要調出「回測好看、實際沒用」的條件，
有集保資料的那段期間切成前後兩半，兩半都變好的調整才值得採用（給使用者挑，模型本身先不改）。
集保只有最近約半年（週資料從開始收的那週起），所以這五個模型的回測其實只有半年左右。
"""

from __future__ import annotations

import time
from bisect import bisect_left
from datetime import date
from typing import Any, Callable

import stock_models as sm

CHIP_KEYS = ["bigInst", "kiloBull", "mainVsRetail", "whaleHot", "bigInstFire"]
# 每週只挑最強的幾檔時，用哪個數字比強弱（大戶持股比這週增加幾個百分點）
STRENGTH_FIELD = {"bigInst": "bigUp", "mainVsRetail": "bigUp", "bigInstFire": "bigUp", "kiloBull": "kiloUp", "whaleHot": "kiloUp"}

WEEKLY_TOP = 10
CONFIRM_DAYS = 5          # 原條件成立後幾個交易日內（含當天）等股價表態
CONFIRM_PCT = 2.0
CONFIRM_VOL_X = 1.5
GAP_MIN_PCT, GAP_MAX_PCT = 3.0, 15.0
LOW_DT_PCT = 20.0

ADJUSTMENTS: list[dict[str, Any]] = [
    {"key": "top10", "label": "每週只挑最強 10 檔",
     "desc": f"同一週集保資料發出的訊號每週最多買 {WEEKLY_TOP} 檔；同一天好幾檔時先挑大戶持股比增加最多的"},
    {"key": "confirm", "label": "等股價表態再買", "except": ["bigInstFire"],   # 合買點火本來就要當天漲 2%、量 1.5 倍
     "desc": f"原條件成立後 {CONFIRM_DAYS} 個交易日內，出現當天漲 ≥ {CONFIRM_PCT:g}%、成交量 ≥ 20 日均量 {CONFIRM_VOL_X:g} 倍那天才買"},
    {"key": "gap", "label": f"離月線 {GAP_MIN_PCT:g}%～{GAP_MAX_PCT:g}%",
     "desc": f"收盤比月線（20 日均線）高 {GAP_MIN_PCT:g}%～{GAP_MAX_PCT:g}%：不貼著月線買，也不追太高"},
    {"key": "revenue", "label": "營收年增 > 0", "except": ["mainVsRetail"],   # 大戶進散戶退本來就要營收年增 > 0 而且加速
     "desc": "最新公布的月營收年增大於 0"},
    {"key": "market", "label": "大盤在月線上", "desc": "加權指數收盤在自己的月線（20 日均線）之上才進場"},
    {"key": "lowDt", "label": f"當沖比改成 < {LOW_DT_PCT:g}%", "only": ["whaleHot"],
     "desc": f"大戶吸籌當沖熱：近 5 天平均當沖比從「≥ 30%」改成「< {LOW_DT_PCT:g}%」（大戶在買、散戶還沒進來）"},
]

STAT_KEYS = ("trades", "open", "winRate", "avgRet", "avgWin", "avgLoss", "profitFactor", "maxConcurrent", "retOnCapital", "mdd")


def adjustments_for(key: str) -> list[dict[str, Any]]:
    """這個模型要比哪些調整：only＝只有這幾個模型比；except＝原條件已經有了，不用再比。"""
    return [a for a in ADJUSTMENTS if key in a.get("only", CHIP_KEYS) and key not in a.get("except", [])]


def market_filter(index: dict[str, list[tuple[str, float]]], name: str = "TAIEX") -> Callable[[str], bool]:
    """那天加權指數收盤是否在自己的 20 日均線之上（前面不到 20 天、沒有指數資料的日子當作不符合）。"""
    rows = index.get(name, [])
    ok: dict[str, bool] = {}
    closes = [c for _, c in rows]
    ma = sm._ma(closes, 20)
    for (d, c), m in zip(rows, ma):
        ok[d] = m is not None and c > m
    return lambda day: ok.get(day, False)


def variant_condition(key: str, flags: set[str], market_ok: Callable[[str], bool]) -> Callable[[sm.StockSeries, int], dict[str, Any] | None]:
    """原條件加上 flags 裡的調整（top10 不在這裡，見 select_trades）。"""
    if key == "whaleHot" and "lowDt" in flags:
        def base(s: sm.StockSeries, i: int) -> dict[str, Any] | None:
            return sm.cond_whale_hot(s, i, low_dt=LOW_DT_PCT)
    else:
        base = sm.CONDITIONS[key]
    memo: dict[tuple[str, int], dict[str, Any] | None] = {}

    def base_at(s: sm.StockSeries, i: int) -> dict[str, Any] | None:
        k = (s.code, i)
        if k not in memo:
            memo[k] = base(s, i) if i >= 0 else None
        return memo[k]

    def cond(s: sm.StockSeries, i: int) -> dict[str, Any] | None:
        if "confirm" in flags:
            if i < 1 or not s.close[i - 1] or s.v20[i] is None or s.v20[i] <= 0:
                return None
            pct = (s.close[i] / s.close[i - 1] - 1) * 100
            if pct < CONFIRM_PCT or s.vol[i] < CONFIRM_VOL_X * s.v20[i]:
                return None
            why = next((w for w in (base_at(s, j) for j in range(i, i - CONFIRM_DAYS, -1)) if w), None)
            if not why:
                return None
            why = {**why, "confirmPct": round(pct, 2), "confirmVolX": round(s.vol[i] / s.v20[i], 2)}
        else:
            why = base_at(s, i)
            if not why:
                return None
        if "gap" in flags:
            if s.ma20[i] is None or s.ma20[i] <= 0:
                return None
            gap = (s.close[i] / s.ma20[i] - 1) * 100
            if not GAP_MIN_PCT <= gap <= GAP_MAX_PCT:
                return None
        if "revenue" in flags:
            rev = sm.revenue_yoy_at(s.revenue, s.dates[i])
            if not rev or rev[1] <= 0:
                return None
        if "market" in flags and not market_ok(s.dates[i]):
            return None
        return why

    return cond


def select_trades(series: dict[str, sm.StockSeries], signals: dict[str, list[tuple[int, dict[str, Any]]]],
                  strength: str, weekly_top: int | None) -> list[dict[str, Any]]:
    """所有股票的訊號照日期排，一筆一筆照回測規則變成交易（同一檔持有中不重複買）。
    weekly_top：同一週集保資料（why["week"]）發出的訊號最多買幾檔；同一天好幾檔先挑 strength 大的。
    只拿當天以前看得到的東西排（同一天的訊號互相比），不偷看後面幾天。"""
    flat = []
    for code, sig in signals.items():
        s = series[code]
        for i, why in sig:
            flat.append((s.dates[i], -float(why.get(strength) or 0), code, i, why))
    flat.sort(key=lambda x: (x[0], x[1], x[2]))
    busy: dict[str, int] = {}
    per_week: dict[str, int] = {}
    trades: list[dict[str, Any]] = []
    for day, _, code, i, why in flat:
        if i <= busy.get(code, -1):
            continue
        week = str(why.get("week") or date.fromisoformat(day).isocalendar()[:2])
        if weekly_top is not None and per_week.get(week, 0) >= weekly_top:
            continue
        got = sm.simulate(series[code], [(i, why)])
        if not got:
            continue   # 隔天開盤就漲停買不到：不佔名額
        per_week[week] = per_week.get(week, 0) + 1
        busy[code] = got[0]["exitIdx"]
        trades.extend(got)
    return trades


def evaluation_window(ctx: dict[str, Any]) -> list[str]:
    """有集保資料可以用的那段回測日：第三週集保之後（千張大戶連增要三週）。"""
    weeks = sorted({w[0] for v in ctx["tdcc"].values() for w in v})
    if len(weeks) < 3:
        return []
    return [d for d in ctx["bt_days"] if d > weeks[2]]


def _stats(trades: list[dict[str, Any]], series: dict[str, sm.StockSeries], days: list[str]) -> dict[str, Any]:
    full = sm.summarize(trades, series, days, {})
    return {k: full.get(k) for k in STAT_KEYS}


def compute_tuning(ctx: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    if ctx.get("status") != "ok":
        return {"status": "empty", "reason": ctx.get("reason")}
    window = evaluation_window(ctx)
    if len(window) < 20:
        return {"status": "empty", "reason": "集保資料還不到一個月"}
    series: dict[str, sm.StockSeries] = ctx["series"]
    mid = window[len(window) // 2]
    parts = {"all": window, "a": [d for d in window if d < mid], "b": [d for d in window if d >= mid]}
    market_ok = market_filter(ctx["index"])
    models_out = []
    for key in CHIP_KEYS:
        spec = next(m for m in sm.MODELS if m["key"] == key)
        adjs = adjustments_for(key)
        variants = [("base", "原版", set())] + [(a["key"], a["label"], {a["key"]}) for a in adjs] + [("all", "全部套用", {a["key"] for a in adjs})]
        out_variants = []
        for vkey, label, flags in variants:
            cond = variant_condition(key, flags, market_ok)
            signals = {}
            for code, s in series.items():
                start = bisect_left(s.dates, window[0])
                if start >= len(s.dates):
                    continue
                sig = sm.signals_for(s, cond, max(1, start))
                if sig:
                    signals[code] = sig
            trades = select_trades(series, signals, STRENGTH_FIELD[key], WEEKLY_TOP if "top10" in flags else None)
            row: dict[str, Any] = {"key": vkey, "label": label}
            for part, days in parts.items():
                # 這一段發訊號的交易；損益算到整段結束（前半段買的、後半段才賣的也要算進前半段）
                picked = [t for t in trades if days[0] <= t["signal"] <= days[-1]]
                row[part] = _stats(picked, series, [d for d in window if d >= days[0]])
            out_variants.append(row)
        models_out.append({"key": key, "name": spec["name"], "variants": out_variants})
    return {
        "status": "ok", "asOf": ctx["until"], "generatedAt": sm._now().isoformat(timespec="seconds"),
        "window": {"from": window[0], "mid": mid, "to": window[-1], "days": len(window)},
        "adjustments": [{k: a[k] for k in ("key", "label", "desc", "only", "except") if k in a} for a in ADJUSTMENTS],
        "rules": [
            "回測規則跟原本一樣：訊號隔天開盤買，持有 10 個交易日，中途收盤跌破月線就賣；每筆 10 萬，已扣手續費和稅",
            "只比有集保資料的這段期間，切成前後兩半；兩半都變好的調整才值得採用",
            "「每週只挑最強 10 檔」以外的調整都是「多一個條件」，所以交易筆數只會變少",
        ],
        "models": models_out,
        "computeSeconds": round(time.monotonic() - started, 1),
    }
