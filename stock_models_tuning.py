"""選股模型：回測比較（給使用者挑要不要改模型；模型本身不受影響）。

2026-11-27 第一輪：只看籌碼的五個模型加六種調整比較，結果只有「大盤在月線上」普遍有用（五個都少賠、勝率都升），
使用者選 A：五個籌碼模型正式加上大盤濾網（stock_models.StockSeries.market_ok）。

2026-11-27 第二輪（使用者選 C），這裡比兩件事：
1. 出場規則：十個模型各用四種出場規則回測。現在約四分之三的交易是「跌破月線」賣掉，
   試試「停損 7%（不看月線）」「抱 20 天」。
2. 籌碼當加分條件：在一年回測最好的「營收起飛爆量」上，再加大戶／法人的籌碼條件，看會不會更好。

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

REV_CHIP: list[dict[str, Any]] = [
    {"key": "base", "label": "營收起飛爆量（原版）", "desc": "原本的條件"},
    {"key": "bigUp", "label": "＋400 張大戶增加", "desc": "集保 400 張以上大戶持股比，最近一週比前一週多"},
    {"key": "kiloUp", "label": "＋千張大戶增加", "desc": "集保 1000 張以上大戶持股比，最近一週比前一週多"},
    {"key": "holdersDown", "label": "＋股東人數減少", "desc": "集保總股東人數，最近一週比前一週少（籌碼集中）"},
    {"key": "inst5", "label": "＋法人近 5 天買超", "desc": "近 5 個交易日外資＋投信合計買超"},
    {"key": "market", "label": "＋大盤在月線上", "desc": "加權指數收盤在月線之上"},
]

STAT_KEYS = ("trades", "open", "winRate", "avgRet", "avgWin", "avgLoss", "profitFactor", "maxConcurrent", "retOnCapital", "mdd")


def rev_chip_condition(extra: str) -> Callable[[sm.StockSeries, int], dict[str, Any] | None]:
    """營收起飛爆量＋一個籌碼條件。"""
    def cond(s: sm.StockSeries, i: int) -> dict[str, Any] | None:
        why = sm.cond_rev_boom(s, i)
        if not why or extra == "base":
            return why
        if extra in ("bigUp", "kiloUp", "holdersDown"):
            weeks = s.weeks_before(i, 2)
            if not weeks:
                return None
            if extra == "bigUp":
                ok = weeks[1][1] > weeks[0][1]
            elif extra == "kiloUp":
                ok = weeks[1][2] > weeks[0][2]
            else:
                ok = 0 < weeks[1][3] < weeks[0][3]
        elif extra == "inst5":
            win = s.inst_window(i, 5)
            ok = bool(win) and sum(f + t for f, t in win) > 0
        elif extra == "market":
            ok = s.market_ok(i)
        else:
            raise ValueError(extra)
        return why if ok else None

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

    # 2. 營收起飛爆量＋籌碼（用到集保，只比有集保的那段）
    rev_chip = None
    if len(chip_win) >= 20:
        variants = []
        for v in REV_CHIP:
            trades = select_trades(series, _signals(series, rev_chip_condition(v["key"]), chip_win))
            variants.append({"key": v["key"], "label": v["label"], **_halves(trades, series, chip_win)})
        rev_chip = {"window": _window_info(chip_win), "variants": variants}

    return {
        "status": "ok", "asOf": ctx["until"], "generatedAt": sm._now().isoformat(timespec="seconds"),
        "exitRules": [{k: r[k] for k in ("key", "label", "desc")} for r in EXIT_RULES],
        "exits": exit_models,
        "revChipRules": [{k: r[k] for k in ("key", "label", "desc")} for r in REV_CHIP],
        "revChip": rev_chip,
        "notes": [
            "進場規則跟原本一樣（訊號隔天開盤買，開盤就漲停買不到的跳過）；每筆 10 萬，已扣手續費和稅",
            "用到集保的模型只比有集保的那段（約半年），其他比整年；都切成前後兩半，兩半都變好的才值得採用",
            "五個籌碼模型已經加上大盤濾網（加權指數在月線上）",
        ],
        "computeSeconds": round(time.monotonic() - started, 1),
    }
