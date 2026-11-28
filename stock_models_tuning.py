"""選股模型：回測比較（給使用者挑要不要改模型；模型本身不受影響）。

2026-11-27 第一輪：只看籌碼的五個模型加六種調整比較，結果只有「大盤在月線上」普遍有用（五個都少賠、勝率都升），
使用者選 A：五個籌碼模型正式加上大盤濾網（stock_models.StockSeries.market_ok）。

2026-11-27 第二輪（使用者選 C），這裡比兩件事：
1. 出場規則：十個模型各用四種出場規則回測。現在約四分之三的交易是「跌破月線」賣掉，
   試試「停損 7%（不看月線）」「抱 20 天」。
2. 籌碼當加分條件：在一年回測最好的「營收起飛爆量」上，再加大戶／法人的籌碼條件，看會不會更好。

第二輪結果：出場規則沒有一致變好（抱 20 天只是放大好壞），維持原樣；營收起飛爆量＋籌碼裡「大盤在月線上」改善最大，
使用者選 1、2：營收起飛爆量正式加上大盤濾網；另外四個不靠集保的模型先比「加／不加大盤濾網」（整年），有效再加。
整年比較後使用者同意：投信卡位營收成長（兩半都變好）、法人先買散戶跟進（前半幾乎一樣、後半少賠）也加；
法人首次進場（一半好一半壞）、多模型共振（幾乎沒差）不加。

第三輪（現在）比：
1. 大盤濾網：營收起飛爆量（已加）、投信卡位營收成長、法人先買散戶跟進、法人首次進場、多模型共振，各自「不加／加」大盤濾網，比整年。
2. 出場規則（同第二輪，留著觀察）。

為了不要調出「回測好看、實際沒用」的規則，每個比較都切成前後兩半，兩半都變好的才值得採用。
集保（大戶持股）只有最近約半年：用到集保的（五個籌碼模型、營收起飛爆量＋集保）只比有集保的那段，其他比整年。
"""

from __future__ import annotations

import time
from bisect import bisect_left
from typing import Any, Callable

import stock_models as sm

TDCC_KEYS = ["bigInst", "kiloBull", "mainVsRetail", "whaleHot", "bigInstFire"]   # 用到集保的五個（共振也有不靠集保的組合，比整年）
STOP_PCT = 7.0

EXIT_RULES: list[dict[str, Any]] = [
    {"key": "base", "label": "原規則", "desc": "抱 10 個交易日；中途收盤跌破月線就賣", "hold": 10, "ma20": True, "stop": None},
    {"key": "stop7", "label": f"停損 {STOP_PCT:g}%", "desc": f"抱 10 個交易日；中途收盤比買進價跌 {STOP_PCT:g}% 以上就賣（不看月線）",
     "hold": 10, "ma20": False, "stop": STOP_PCT},
    {"key": "hold20", "label": "抱 20 天", "desc": "抱 20 個交易日；中途收盤跌破月線就賣", "hold": 20, "ma20": True, "stop": None},
    {"key": "hold20stop7", "label": f"抱 20 天＋停損 {STOP_PCT:g}%", "desc": f"抱 20 個交易日；中途收盤跌 {STOP_PCT:g}% 以上就賣（不看月線）",
     "hold": 20, "ma20": False, "stop": STOP_PCT},
]

MARKET_KEYS = ["revBoom", "trustGrowth", "instThenRetail", "instFirst", "multiCombo"]   # 不靠集保的五個（營收起飛爆量已經加了）
MARKET_VARIANTS = [("off", "不加大盤濾網"), ("on", "加大盤濾網（加權指數在月線上）")]

# 強勢族群拉回（第 11 個模型）：拉回買 vs 突破就追、停損方式、抱幾天、族群那一層有沒有用
PULLBACK_VARIANTS: list[dict[str, Any]] = [
    {"key": "base", "label": "拉回買（正式版）", "desc": "拉回到 5 日線附近、隔天突破高點才買；停損 8% 和拉回低點取近的；最多抱 5 天",
     "setup": "pullback", "group": True, "entry": "trigger", "stop": "near", "hold": 5},
    {"key": "breakout", "label": "突破就追", "desc": "同樣的選股條件，帶量創 20 日新高（漲 ≥ 2%、量 ≥ 1.5 倍）隔天開盤就買",
     "setup": "breakout", "group": True, "entry": "open", "stop": "near", "hold": 5},
    {"key": "fixed8", "label": "停損只看 8%", "desc": "不看拉回低點，只在跌 8% 時停損",
     "setup": "pullback", "group": True, "entry": "trigger", "stop": "fixed", "hold": 5},
    {"key": "hold3", "label": "最多抱 3 天", "desc": "其他一樣，最多抱 3 個交易日",
     "setup": "pullback", "group": True, "entry": "trigger", "stop": "near", "hold": 3},
    {"key": "noGroup", "label": "不篩族群", "desc": "其他一樣，不限最強 5 個族群（看族群這一層有沒有幫助）",
     "setup": "pullback", "group": False, "entry": "trigger", "stop": "near", "hold": 5},
]

# 弱勢族群當沖空（第 12 個模型）：三種進場、停損 4% vs 8%、族群和券資比那兩層有沒有用
DT_SHORT_VARIANTS: list[dict[str, Any]] = [
    {"key": "breakdown", "label": "跌破昨低才空（正式版）", "desc": "隔天盤中跌破前一天最低價才空；停損 4%；收盤回補",
     "entry": "breakdown", "stop": 4.0, "group": True, "ratio": True},
    {"key": "open", "label": "開盤就空", "desc": "名單上的股票隔天開盤就空", "entry": "open", "stop": 4.0, "group": True, "ratio": True},
    {"key": "rebound", "label": "反彈過不去再空", "desc": "盤中反彈到昨收或 5 日線（取低的）才空；開盤就在上面的不空",
     "entry": "rebound", "stop": 4.0, "group": True, "ratio": True},
    {"key": "stop8", "label": "跌破昨低・停損 8%", "desc": "參考用：停損放寬到 8%", "entry": "breakdown", "stop": 8.0, "group": True, "ratio": True},
    {"key": "noGroup", "label": "不篩族群", "desc": "其他一樣，不限最弱 5 個族群", "entry": "breakdown", "stop": 4.0, "group": False, "ratio": True},
    {"key": "noRatio", "label": "不看券資比", "desc": "其他一樣，不限券資比 ≤ 30%", "entry": "breakdown", "stop": 4.0, "group": True, "ratio": False},
]

STAT_KEYS = ("trades", "open", "winRate", "avgRet", "avgWin", "avgLoss", "profitFactor", "maxConcurrent", "retOnCapital", "mdd")


def market_condition(key: str, with_market: bool) -> Callable[[sm.StockSeries, int], dict[str, Any] | None]:
    """這個模型加／不加大盤濾網。正式版已經有濾網的（sm.CORE_CONDITIONS）：不加＝只用加之前的原條件。"""
    base = sm.CORE_CONDITIONS.get(key) or sm.CONDITIONS[key]
    if not with_market:
        return base

    def cond(s: sm.StockSeries, i: int) -> dict[str, Any] | None:
        return base(s, i) if s.market_ok(i) else None

    return cond


def select_trades(series: dict[str, sm.StockSeries], signals: dict[str, list[tuple[int, dict[str, Any]]]], *,
                  hold_days: int = sm.HOLD_DAYS, ma20_exit: bool = True, stop_pct: float | None = None) -> list[dict[str, Any]]:
    """每檔股票的訊號照回測規則變成交易（同一檔持有中不重複買），出場規則可換。"""
    trades: list[dict[str, Any]] = []
    for code, sig in signals.items():
        trades.extend(sm.simulate(series[code], sig, hold_days=hold_days, ma20_exit=ma20_exit, stop_pct=stop_pct))
    return trades


def tdcc_window(ctx: dict[str, Any]) -> list[str]:
    """有集保資料可以用的那段回測日：第三週集保之後（千張大戶連增要三週）。"""
    weeks = sorted({w[0] for v in ctx["tdcc"].values() for w in v})
    if len(weeks) < 3:
        return []
    return [d for d in ctx["bt_days"] if d > weeks[2]]


def _stats(trades: list[dict[str, Any]], series: dict[str, sm.StockSeries], days: list[str]) -> dict[str, Any]:
    full = sm.summarize(trades, series, days, {})
    out = {k: full.get(k) for k in STAT_KEYS}
    closed = [t for t in trades if not t["open"]]
    out["avgDays"] = round(sum(t["exitIdx"] - t["entryIdx"] + 1 for t in closed) / len(closed), 1) if closed else None
    out["stopRate"] = round(sum(1 for t in closed if t["reason"] in ("ma20", "stop")) / len(closed) * 100, 1) if closed else None
    return out


def _signals(series: dict[str, sm.StockSeries], cond: Callable[[sm.StockSeries, int], Any], window: list[str]) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    out = {}
    for code, s in series.items():
        start = bisect_left(s.dates, window[0])
        if start >= len(s.dates):
            continue
        sig = sm.signals_for(s, cond, max(1, start))
        if sig:
            out[code] = sig
    return out


def _halves(trades: list[dict[str, Any]], series: dict[str, sm.StockSeries], window: list[str]) -> dict[str, Any]:
    """整段／前半／後半（照發訊號的日子分）。損益算到整段結束：前半段買、後半段才賣的也算前半段。"""
    mid = window[len(window) // 2]
    out = {}
    for part, days in (("all", window), ("a", [d for d in window if d < mid]), ("b", [d for d in window if d >= mid])):
        picked = [t for t in trades if days[0] <= t["signal"] <= days[-1]]
        out[part] = _stats(picked, series, [d for d in window if d >= days[0]])
    return out


def _window_info(window: list[str]) -> dict[str, Any]:
    return {"from": window[0], "mid": window[len(window) // 2], "to": window[-1], "days": len(window)}


def compute_tuning(ctx: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    if ctx.get("status") != "ok":
        return {"status": "empty", "reason": ctx.get("reason")}
    series: dict[str, sm.StockSeries] = ctx["series"]
    full = list(ctx["bt_days"])
    chip_win = tdcc_window(ctx)
    if len(full) < 40:
        return {"status": "empty", "reason": "回測日不夠"}

    # 1. 出場規則：十個模型 × 四種
    exit_models = []
    for spec in sm.MODELS:
        key = spec["key"]
        window = chip_win if key in TDCC_KEYS else full
        if len(window) < 20:
            continue
        signals = _signals(series, sm.CONDITIONS[key], window)
        variants = []
        for rule in EXIT_RULES:
            trades = select_trades(series, signals, hold_days=rule["hold"], ma20_exit=rule["ma20"], stop_pct=rule["stop"])
            variants.append({"key": rule["key"], "label": rule["label"], **_halves(trades, series, window)})
        exit_models.append({"key": key, "name": spec["name"], "window": _window_info(window), "variants": variants})

    # 2. 大盤濾網：不靠集保的五個，整年
    market = []
    for key in MARKET_KEYS:
        spec = next(m for m in sm.MODELS if m["key"] == key)
        variants = []
        for vkey, label in MARKET_VARIANTS:
            trades = select_trades(series, _signals(series, market_condition(key, vkey == "on"), full))
            variants.append({"key": vkey, "label": label, **_halves(trades, series, full)})
        market.append({"key": key, "name": spec["name"], "current": "on" if key in sm.CORE_CONDITIONS else "off",
                       "window": _window_info(full), "variants": variants})

    # 3. 強勢族群拉回：整年
    pullback = []
    for v in PULLBACK_VARIANTS:
        def cond(s: sm.StockSeries, i: int, v: dict[str, Any] = v) -> dict[str, Any] | None:
            return sm.group_pullback_signal(s, i, setup=v["setup"], need_group=v["group"])

        trades = []
        for code, sig in _signals(series, cond, full).items():
            trades.extend(sm.simulate_pullback(series[code], sig, entry=v["entry"], stop_mode=v["stop"], hold_days=v["hold"]))
        pullback.append({"key": v["key"], "label": v["label"], **_halves(trades, series, full)})

    # 4. 弱勢族群當沖空：整年（同樣的選股條件只算一次訊號）
    dt_short = []
    sig_cache: dict[tuple[bool, bool], dict[str, list[tuple[int, dict[str, Any]]]]] = {}
    for v in DT_SHORT_VARIANTS:
        key = (v["group"], v["ratio"])
        if key not in sig_cache:
            def cond(s: sm.StockSeries, i: int, g: bool = v["group"], r: bool = v["ratio"]) -> dict[str, Any] | None:
                return sm.short_signal(s, i, need_group=g, max_ratio=sm.SHORT_MAX_RATIO if r else None)

            out: dict[str, list[tuple[int, dict[str, Any]]]] = {}
            for code, s in series.items():
                start = bisect_left(s.dates, full[0])
                if start < len(s.dates):
                    sig = sm.level_signals(s, cond, max(1, start))
                    if sig:
                        out[code] = sig
            sig_cache[key] = out
        trades = []
        for code, sig in sig_cache[key].items():
            trades.extend(sm.simulate_dt_short(series[code], sig, entry=v["entry"], stop_pct=v["stop"]))
        dt_short.append({"key": v["key"], "label": v["label"], **_halves(trades, series, full)})

    return {
        "status": "ok", "asOf": ctx["until"], "generatedAt": sm._now().isoformat(timespec="seconds"),
        "exitRules": [{k: r[k] for k in ("key", "label", "desc")} for r in EXIT_RULES],
        "exits": exit_models,
        "market": market,
        "pullbackRules": [{k: v[k] for k in ("key", "label", "desc")} for v in PULLBACK_VARIANTS],
        "pullback": {"window": _window_info(full), "variants": pullback},
        "dtShortRules": [{k: v[k] for k in ("key", "label", "desc")} for v in DT_SHORT_VARIANTS],
        "dtShort": {"window": _window_info(full), "variants": dt_short},
        "notes": [
            "進場規則跟原本一樣（訊號隔天開盤買，開盤就漲停買不到的跳過）；每筆 10 萬，已扣手續費和稅",
            "用到集保的模型只比有集保的那段（約半年），其他比整年；都切成前後兩半，兩半都變好的才值得採用",
            "已經加上大盤濾網（加權指數在月線上）的：五個籌碼模型、營收起飛爆量、投信卡位營收成長、法人先買散戶跟進；current＝正式版現在用哪一個",
        ],
        "computeSeconds": round(time.monotonic() - started, 1),
    }
