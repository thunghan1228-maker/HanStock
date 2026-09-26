"""波段日報（第一階段）。

使用者 2026-09-26 貼「波段精選日報」的截圖當規格：每個交易日收盤後整理一份隔天盤前讀的報告，
今日摘要、產業觀察、籌碼面精選、技術面精選、均線轉強，每檔附防守價與風險提示，可回看近 10 個交易日。
第一階段只用現有資料：族群個股的日K（均線分數、月線、季線、三日低、5 日均量）、三大法人每日買賣超（5 日合計、連續天數）、
主力大單每日淨額（5 日合計、連續天數）、處置股清單。集保週籌碼、處置動態、本益比、月營收、主動式 ETF 是第二、三階段。

報告以「資料基準日」（最後一根日K的日期）為鍵存進 swing_reports 表；同一天重算就覆蓋（法人資料 16:40 才齊，先算的版本會被補齊後的蓋掉）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from brew_launch import MA_PERIODS, _latest_bar_date, _load_bars, _load_market_values, group_codes, ma_alignment_score, skipped_codes
from brew_launch_history import group_and_name
from chips_daily import _institutional_by_date, _streak, main_force_daily, main_force_dates, stored_dates
from database import get_connection, initialize_database
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS
from trading_days import is_trading_day

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))

KEEP_DAYS = 40                 # 表裡最多留幾天
LOOKBACK_DATES = 10            # 前端可回看的天數
PICK_LIMIT = 5                 # 每個精選名單最多幾檔
INST_DAYS = 5                  # 法人／主力「5 日」
TECH_MIN_ABOVE_PCT = 3.0       # 真穿月線：收盤站上月線 ≥3%
TECH_MIN_VOLUME = 500          # 真穿月線：量 ≥500 張
MA_JUMP_MIN = 3                # 均線轉強：均線分數比前一交易日跳升 ≥3
MA_STRONG_MIN = 8              # 均線轉強：跳升後至少 8 分
DEV5_WARN_PCT = 15.0           # 與 5 日線乖離 ≥15% 提示
CHIPS_MIN_STREAK = 3           # 籌碼面：法人連買 ≥3 天
CHIPS_MIN_MF_STREAK = 2        # 籌碼面：或主力連買 ≥2 天
CHIPS_MIN_INST5_PCT = 1.0      # 籌碼面：或法人 5 日買超 ≥ 股本 1%
HOT_GROUP_RANK = 10            # 熱門族：族群當天漲幅前 10
POLL_SECONDS = 15 * 60
REFRESH_START_MINUTE = 15 * 60 + 5    # 交易日 15:05 起重算今天的
REFRESH_END_MINUTE = 18 * 60


# ------------------------------------------------------------------ 儲存

def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS swing_reports (
            report_date TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )"""
    )


def save_report(payload: dict[str, Any]) -> None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.execute(
            "INSERT OR REPLACE INTO swing_reports (report_date, generated_at, payload) VALUES (?, ?, ?)",
            (payload["date"], payload["generatedAt"], json.dumps(payload, ensure_ascii=False)),
        )
        connection.execute(
            "DELETE FROM swing_reports WHERE report_date NOT IN (SELECT report_date FROM swing_reports ORDER BY report_date DESC LIMIT ?)",
            (KEEP_DAYS,),
        )


def load_report(report_date: str) -> dict[str, Any] | None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT payload FROM swing_reports WHERE report_date = ?", (report_date,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"])
    except (TypeError, ValueError):
        return None


def report_dates(limit: int = LOOKBACK_DATES) -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT report_date FROM swing_reports ORDER BY report_date DESC LIMIT ?", (limit,)).fetchall()
    return [str(r["report_date"]) for r in rows]


def bar_dates(limit: int = LOOKBACK_DATES) -> list[str]:
    """最近幾個有日K的交易日（新的在前）。"""
    initialize_database()
    with get_connection() as connection:
        rows = connection.execute("SELECT DISTINCT substr(bar_time, 1, 10) AS d FROM bars_1d ORDER BY d DESC LIMIT ?", (limit,)).fetchall()
    return [str(r["d"]) for r in rows if r["d"]]


# ------------------------------------------------------------------ 個股技術面

def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _r2(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def analyze_bars(bars: list[tuple[str, float, float, float, int]]) -> dict[str, Any] | None:
    """bars 舊到新（日期, 高, 低, 收, 量張），最後一根就是資料基準日。日K不到 21 根算不出月線就回 None。"""
    n = len(bars)
    if n < 21:
        return None
    closes = [b[3] for b in bars]
    highs = [b[1] for b in bars]
    lows = [b[2] for b in bars]
    vols = [b[4] for b in bars]
    close, prev = closes[-1], closes[-2]
    ma = {p: _mean(closes[-p:]) for p in (5, 10, 20, 60) if n >= p}
    prev_ma20 = _mean(closes[-21:-1])
    score = ma_alignment_score({p: _mean(closes[-p:]) for p in MA_PERIODS}) if n >= max(MA_PERIODS) else None
    prev_score = ma_alignment_score({p: _mean(closes[-p - 1:-1]) for p in MA_PERIODS}) if n >= max(MA_PERIODS) + 1 else None
    ma20 = ma[20]
    change_pct = (close / prev - 1) * 100 if prev > 0 else 0.0
    above_ma20_pct = (close / ma20 - 1) * 100 if ma20 > 0 else 0.0
    avg_vol5 = _mean([float(v) for v in vols[-5:]])
    return {
        "asOf": bars[-1][0],
        "close": close, "prevClose": prev, "changePct": round(change_pct, 2), "volume": vols[-1],
        "ma5": _r2(ma.get(5)), "ma10": _r2(ma.get(10)), "ma20": _r2(ma20), "ma60": _r2(ma.get(60)),
        "score": score, "prevScore": prev_score,
        "aboveMa20": close > ma20,
        "aboveMa20Pct": round(above_ma20_pct, 2),
        "crossedMa20": prev <= prev_ma20 and close > ma20,        # 今天才站上月線
        "threeDayLow": min(lows[-3:]),
        "ma60OverHead": (ma.get(60) is not None and ma[60] > close),
        "dev5Pct": round((close / ma[5] - 1) * 100, 2) if ma.get(5) else None,
        "limitUp": change_pct >= 9.4,
        "volumeRatio": round(vols[-1] / avg_vol5, 2) if avg_vol5 > 0 else None,
        "boxHigh": max(highs[-10:]), "boxLow": min(lows[-10:]),
        "newHigh20": n >= 21 and close > max(highs[-21:-1]),
        "bars": n,
    }


# ------------------------------------------------------------------ 報告

def _next_day(date_str: str) -> str:
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def _lots(shares: int | None) -> float | None:
    return None if shares is None else round(shares / 1000, 1)


def _risks(t: dict[str, Any], chips: dict[str, Any], disposed: bool) -> list[str]:
    out: list[str] = []
    if disposed:
        out.append("處置中")
    if t.get("ma60OverHead") and t.get("ma60"):
        out.append(f"季線 {t['ma60']:.2f} 在頭上")
    dev5 = t.get("dev5Pct")
    if dev5 is not None and dev5 >= DEV5_WARN_PCT:
        out.append(("漲停後" if t.get("limitUp") else "") + f"與 5 日線乖離 {dev5:.0f}%")
    if chips.get("inst5") is not None and chips["inst5"] < 0:
        out.append("法人 5 日仍為賣超")
    if chips.get("instToday") is not None and chips["instToday"] < 0 and (chips.get("inst5") or 0) > 0:
        out.append("法人今天轉賣超")
    return out


def _card(code: str, t: dict[str, Any], chips: dict[str, Any], group: str, hot: bool, disposed: bool, tag: str) -> dict[str, Any]:
    _g, name = group_and_name(code)
    return {
        "code": code, "name": name, "group": group, "hot": hot, "tag": tag,
        "close": t["close"], "changePct": t["changePct"], "volume": t["volume"],
        "score": t["score"], "prevScore": t["prevScore"], "aboveMa20Pct": t["aboveMa20Pct"],
        "inst5": chips.get("inst5"), "inst5Pct": chips.get("inst5Pct"), "instStreak": chips.get("instStreak", 0), "instToday": chips.get("instToday"),
        "mf5": chips.get("mf5"), "mfStreak": chips.get("mfStreak", 0),
        "defense": {"ma20": t["ma20"], "threeDayLow": t["threeDayLow"]},
        "risks": _risks(t, chips, disposed),
        "health": chips.get("health"), "healthChecks": chips.get("healthChecks"),
    }


def _health(t: dict[str, Any], chips: dict[str, Any]) -> tuple[int, list[str]]:
    """體質七項：站上月線、均線分數≥10、法人 5 日買超、主力 5 日買超、法人連買≥2 天、量≥5 日均量、今天收漲。"""
    checks = [
        ("站上月線", bool(t.get("aboveMa20"))),
        ("均線分數≥10", (t.get("score") or 0) >= 10),
        ("法人 5 日買超", (chips.get("inst5") or 0) > 0),
        ("主力 5 日買超", (chips.get("mf5") or 0) > 0),
        ("法人連買≥2 天", (chips.get("instStreak") or 0) >= 2),
        ("量≥5 日均量", (t.get("volumeRatio") or 0) >= 1.0),
        ("今天收漲", (t.get("changePct") or 0) > 0),
    ]
    return sum(1 for _label, ok in checks if ok), [label for label, ok in checks if ok]


def _judge_group(ratio: float, inst5: float, has_inst: bool) -> str:
    if not has_inst:
        return "法人資料還沒進來，先看型態" + ("：多數已站上月線" if ratio >= 0.5 else "：多數還在月線下")
    if ratio >= 0.5 and inst5 > 0:
        return "資金已進、型態已翻，只挑籌碼連續上榜者"
    if inst5 > 0:
        return "法人先進、型態未翻：低檔醞釀，等站上月線再進"
    if ratio >= 0.5:
        return "型態已翻、法人未跟：只挑法人連買的個股"
    return "資金未進、型態未翻：先看族群再看個股"


def build_report(as_of: str | None = None, *, disposition_codes: set[str] | None = None) -> dict[str, Any]:
    """整理一天的波段日報。as_of 沒給就用最後一根日K的日期。"""
    codes = group_codes()
    skipped = skipped_codes()
    disposed = {c.upper() for c in (disposition_codes or set())}
    if not as_of:
        as_of = _latest_bar_date()
    if not as_of:
        return {"status": "empty", "date": None, "reason": "還沒有日K資料"}
    bars_by_code = _load_bars(codes, session=_next_day(as_of))
    market_values = _load_market_values(codes, session=_next_day(as_of))
    tech: dict[str, dict[str, Any]] = {}
    stale = 0
    for code, bars in bars_by_code.items():
        if not bars or bars[-1][0] != as_of:
            stale += 1
            continue
        info = analyze_bars(bars)
        if info:
            tech[code] = info
    # 籌碼：法人 5 日（最近 5 個有資料的交易日，≤ 基準日）與主力 5 日
    inst_dates = [d for d in stored_dates(30) if d <= as_of][:INST_DAYS]
    inst_by_date = _institutional_by_date(inst_dates, codes) if inst_dates else {}
    mf_dates = [d for d in main_force_dates(30) if d <= as_of][:INST_DAYS]
    mf_by_date = {d: main_force_daily(d, codes) for d in mf_dates}
    chips: dict[str, dict[str, Any]] = {}
    for code in codes:
        entry: dict[str, Any] = {"inst5": None, "inst5Pct": None, "instStreak": 0, "instToday": None, "mf5": None, "mfStreak": 0}
        totals = [(inst_by_date.get(d, {}).get(code) or {}).get("total") for d in inst_dates]
        known = [v for v in totals if v is not None]
        if known:
            entry["inst5"] = _lots(sum(known))
            entry["instStreak"] = _streak(totals)
            today = (inst_by_date.get(as_of, {}).get(code) or {}).get("total") if as_of in inst_by_date else None
            entry["instToday"] = _lots(today) if today is not None else None
            t = tech.get(code)
            mv = market_values.get(code)
            if t and mv and t["close"] > 0:
                shares_lots = mv[1] / t["close"] / 1000
                if shares_lots > 0:
                    entry["inst5Pct"] = round(sum(known) / 1000 / shares_lots * 100, 2)
        nets = [(mf_by_date.get(d, {}).get(code) or {}).get("net") for d in mf_dates]
        known_mf = [v for v in nets if v is not None]
        if known_mf:
            entry["mf5"] = int(sum(known_mf))
            entry["mfStreak"] = _streak(nets)
        t = tech.get(code)
        if t:
            entry["health"], entry["healthChecks"] = _health(t, entry)
        chips[code] = entry
    has_inst = bool(inst_dates) and inst_dates[0] == as_of
    # 族群
    groups: list[dict[str, Any]] = []
    for name, members in STOCK_GROUPS.items():
        if name in SPECIAL_GROUP_NAMES:
            continue
        member_codes = [str(c).strip().upper() for c, _n in members]
        fresh = [c for c in member_codes if c in tech]
        if not fresh:
            continue
        above = sum(1 for c in fresh if tech[c]["aboveMa20"])
        inst5 = sum((chips[c]["inst5"] or 0) for c in fresh if c in chips)
        scores = [tech[c]["score"] for c in fresh if tech[c]["score"] is not None]
        groups.append({
            "name": name, "members": len(fresh), "aboveMa20": above, "aboveRatio": round(above / len(fresh), 2),
            "inst5": round(inst5, 1), "avgChange": round(_mean([tech[c]["changePct"] for c in fresh]), 2),
            "avgScore": round(_mean(scores), 1) if scores else None,
            "crossed": sum(1 for c in fresh if tech[c]["crossedMa20"]),
        })
    groups.sort(key=lambda g: g["avgChange"], reverse=True)
    for i, g in enumerate(groups):
        g["rank"] = i + 1
        g["hot"] = i < HOT_GROUP_RANK
        g["judge"] = _judge_group(g["aboveRatio"], g["inst5"], has_inst)
    group_rank = {g["name"]: g["rank"] for g in groups}
    group_of = {}
    for name, members in STOCK_GROUPS.items():
        if name in SPECIAL_GROUP_NAMES:
            continue
        for c, _n in members:
            group_of.setdefault(str(c).strip().upper(), name)

    def card(code: str, tag: str) -> dict[str, Any]:
        g = group_of.get(code, "")
        return _card(code, tech[code], chips.get(code, {}), g, group_rank.get(g, 99) <= HOT_GROUP_RANK, code in disposed, tag)

    eligible = [c for c in tech if c not in skipped]
    # 籌碼面精選：法人 5 日買超，且法人連買≥3 天或主力連買≥2 天或 5 日買超≥股本 1%；法人 5 日佔股本比例高的在前
    chips_all = []
    for c in eligible:
        ch = chips.get(c) or {}
        if (ch.get("inst5") or 0) <= 0:
            continue
        if ch["instStreak"] >= CHIPS_MIN_STREAK or ch["mfStreak"] >= CHIPS_MIN_MF_STREAK or (ch.get("inst5Pct") or 0) >= CHIPS_MIN_INST5_PCT:
            chips_all.append(c)
    chips_all.sort(key=lambda c: ((chips[c].get("inst5Pct") or 0), chips[c]["inst5"] or 0), reverse=True)
    chips_picks = [card(c, (f"法人連買 {chips[c]['instStreak']} 天" if chips[c]["instStreak"] >= CHIPS_MIN_STREAK else f"主力連買 {chips[c]['mfStreak']} 天" if chips[c]["mfStreak"] >= CHIPS_MIN_MF_STREAK else "法人 5 日大買")) for c in chips_all[:PICK_LIMIT]]
    # 技術面精選：今天才站上月線（真穿）：站上 ≥3% 且量 ≥500 張；剛站上未達門檻的只算數
    crossed = [c for c in eligible if tech[c]["crossedMa20"]]
    tech_all = [c for c in crossed if tech[c]["aboveMa20Pct"] >= TECH_MIN_ABOVE_PCT and tech[c]["volume"] >= TECH_MIN_VOLUME]
    tech_all.sort(key=lambda c: tech[c]["aboveMa20Pct"], reverse=True)
    tech_picks = [card(c, f"站上 +{tech[c]['aboveMa20Pct']:.1f}%") for c in tech_all[:PICK_LIMIT]]
    # 均線轉強：均線分數比前一交易日跳升 ≥3 且 ≥8 分；跳得多、體質好的在前
    ma_all = [c for c in eligible if tech[c]["score"] is not None and tech[c]["prevScore"] is not None
              and tech[c]["score"] - tech[c]["prevScore"] >= MA_JUMP_MIN and tech[c]["score"] >= MA_STRONG_MIN]
    ma_all.sort(key=lambda c: (tech[c]["score"] - tech[c]["prevScore"], chips.get(c, {}).get("health") or 0, tech[c]["score"]), reverse=True)
    ma_picks = [card(c, f"均線 {tech[c]['prevScore']}→{tech[c]['score']}") for c in ma_all[:PICK_LIMIT]]
    new_full = [c for c in eligible if tech[c]["score"] == 15 and tech[c]["prevScore"] is not None and tech[c]["prevScore"] < 15]
    # 今日摘要
    main_groups = [g for g in groups if g["aboveRatio"] >= 0.5 and g["inst5"] > 0]
    main_groups.sort(key=lambda g: g["inst5"], reverse=True)
    brewing_groups = [g for g in groups if g["inst5"] > 0 and g["aboveRatio"] < 0.5]
    brewing_groups.sort(key=lambda g: g["inst5"], reverse=True)
    names = lambda cards: "、".join(x["name"] for x in cards)  # noqa: E731
    summary: list[str] = []
    if not has_inst:
        summary.append("法人資料還沒進來（上市約 15:00、上櫃約 16:40），籌碼相關的判斷等資料齊了會自動更新")
    summary.append(("資金主軸在已站上月線的族群：" + "、".join(g["name"] for g in main_groups[:3]) + " —— 法人同步買超")
                   if main_groups else "今天沒有族群同時「多數站上月線」又「法人 5 日買超」，資金沒有明顯主軸")
    if brewing_groups:
        summary.append("法人先進、型態未翻：" + "、".join(g["name"] for g in brewing_groups[:2]) + " —— 低檔醞釀，等站上月線再進")
    summary.append("籌碼面首選 " + (names(chips_picks[:2]) or "無") + "；技術面首選 " + (names(tech_picks[:3]) or "無"))
    summary.append("均線轉強 " + (names(ma_picks[:2]) or "無") + "；均線新滿分 " + ("、".join(group_and_name(c)[1] for c in new_full[:3]) or "無"))
    risk_lines = [f"{x['name']} {r}" for x in chips_picks + tech_picks + ma_picks for r in x["risks"]]
    summary.append("風險提示：" + ("；".join(risk_lines[:4]) if risk_lines else "精選名單沒有特別的風險提示"))
    return {
        "status": "ok",
        "date": as_of,
        "generatedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"),
        "basis": {"instDates": inst_dates, "mfDates": mf_dates, "hasInst": has_inst, "stocks": len(tech), "stale": stale,
                  "withScore": sum(1 for t in tech.values() if t["score"] is not None)},
        "tiles": {"crossed": len(crossed), "crossedQualified": len(tech_all), "maJump": len(ma_all), "chips": len(chips_all),
                  "disposition": len([c for c in codes if c in disposed]), "newFull": len(new_full)},
        "summary": summary,
        "groups": groups,
        "picks": {"chips": chips_picks, "tech": tech_picks, "ma": ma_picks},
        "counts": {"chips": len(chips_all), "tech": len(tech_all), "techNear": len(crossed) - len(tech_all), "ma": len(ma_all)},
        "rules": {
            "chips": f"法人 5 日買超，且法人連買≥{CHIPS_MIN_STREAK} 天、主力連買≥{CHIPS_MIN_MF_STREAK} 天或 5 日買超≥股本 {CHIPS_MIN_INST5_PCT:g}%；法人 5 日佔股本比例高的在前",
            "tech": f"今天收盤才站上月線（前一天在月線下）、站上 ≥{TECH_MIN_ABOVE_PCT:g}%、量 ≥{TECH_MIN_VOLUME} 張；站上月線第一天，防守就是月線本身",
            "ma": f"均線分數比前一交易日跳升 ≥{MA_JUMP_MIN} 且 ≥{MA_STRONG_MIN} 分；體質＝站上月線、均線分數≥10、法人 5 日買超、主力 5 日買超、法人連買≥2 天、量≥5 日均量、今天收漲，七項各 1 分",
            "risks": f"季線在頭上、與 5 日線乖離 ≥{DEV5_WARN_PCT:g}%、法人 5 日仍賣超、法人今天轉賣超、處置中",
        },
    }


# ------------------------------------------------------------------ 端點用

def _disposition_codes() -> set[str]:
    try:
        from disposition_stocks import get_disposition_map

        return {str(c).strip().upper() for c in get_disposition_map().keys()}
    except Exception:  # noqa: BLE001
        return set()


def refresh_report(as_of: str | None = None) -> dict[str, Any]:
    payload = build_report(as_of, disposition_codes=_disposition_codes())
    if payload.get("status") == "ok":
        save_report(payload)
    return payload


def swing_report(date: str | None = None) -> dict[str, Any]:
    """前端要的：指定日期的報告（沒存過就當場算並存起來）；沒給日期就是最新一份。"""
    dates = report_dates(LOOKBACK_DATES)
    if date:
        payload = load_report(date)
        if payload is None and date in bar_dates(LOOKBACK_DATES + 5):
            payload = refresh_report(date)
            dates = report_dates(LOOKBACK_DATES)
        if payload is None:
            return {"status": "empty", "date": date, "dates": dates, "reason": "那一天沒有報告"}
    else:
        latest = _latest_bar_date()
        payload = load_report(latest) if latest else None
        if payload is None and latest:
            payload = refresh_report(latest)
            dates = report_dates(LOOKBACK_DATES)
        if payload is None:
            return {"status": "empty", "date": None, "dates": dates, "reason": "還沒有日K資料"}
    return {**payload, "dates": dates, "collector": collector_status()}


# ------------------------------------------------------------------ 收集器

_state: dict[str, Any] = {"running": False, "lastRunAt": None, "lastDate": None, "lastError": None, "backfilled": 0}
_lock = threading.Lock()


def collector_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def run_once(now: datetime | None = None) -> dict[str, Any]:
    """重算最新一天；沒存過報告的近幾個交易日（有日K的）也補起來。"""
    now = now or datetime.now(TW_TZ)
    latest = _latest_bar_date()
    result: dict[str, Any] = {"date": latest, "backfilled": []}
    if not latest:
        return result
    try:
        refresh_report(latest)
        have = set(report_dates(LOOKBACK_DATES + 5))
        for d in bar_dates(LOOKBACK_DATES):
            if d not in have and d != latest:
                refresh_report(d)
                result["backfilled"].append(d)
        with _lock:
            _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastDate": latest, "lastError": None,
                           "backfilled": _state["backfilled"] + len(result["backfilled"])})
    except Exception as exc:  # noqa: BLE001
        logger.exception("swing report failed")
        with _lock:
            _state.update({"lastRunAt": now.isoformat(timespec="seconds"), "lastError": str(exc)})
        result["error"] = str(exc)
    return result


def _in_refresh_window(now: datetime) -> bool:
    if not is_trading_day(now):
        return False
    minute = now.hour * 60 + now.minute
    return REFRESH_START_MINUTE <= minute <= REFRESH_END_MINUTE


def _loop() -> None:
    time.sleep(90)   # 讓其他收集器先跑（日K、法人）
    run_once()
    last_window_run: str | None = None
    while True:
        time.sleep(POLL_SECONDS)
        now = datetime.now(TW_TZ)
        if _in_refresh_window(now):
            run_once(now)
            last_window_run = now.strftime("%Y-%m-%d")
        elif last_window_run != now.strftime("%Y-%m-%d") and now.hour >= 18 and is_trading_day(now):
            # 18:00 後補一次（收集器啟動晚、或視窗內都失敗）
            run_once(now)
            last_window_run = now.strftime("%Y-%m-%d")


def start_swing_report_collector() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_loop, name="swing-report", daemon=True).start()
    return True
