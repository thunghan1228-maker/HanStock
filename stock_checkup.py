"""每日持股健診（2026-09-28 使用者：照學員專區「每日持股健診」與「個股問診」做）第一階段。

每個交易日收盤後，把有日K的每一檔算好存起來（checkup_daily，每檔一筆 JSON）：
- 三面向分數（0～100）：基本面＝月營收年增／月增＋本益比位階；籌碼面＝集保 400 張以上大戶週增＋法人 5 日＋主力 5 日；
  技術面＝內定均線分數換算成百分。綜合＝有資料的面向加權平均（預設 34／33／33，前端可自訂）
- 防守線：三日低（不含當天的前三個交易日最低；明日值＝含今天的近三日最低）、月線（20 日收盤均）、
  紅半（近三個交易日最高與最低的中間值；三日振幅 ≥10% 且收盤站在中點之上 1% 以上才用）。
  今日判定＝今收 vs 昨日的線：收盤跌破任一條減碼、月線與三日低雙破出場；即將穿惡＝收盤在月線下但距月線 10% 以內
- 七科小體檢（均線、族群、族內名次、籌碼、本益比、營收、法人，各 0～2 分）、紅綠燈、強勢／中等／弱勢
貼一串股號就一次排出來比（/api/hub/checkup）；之後的個股問診也用這份。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from brew_launch import MA_PERIODS, _latest_bar_date, ma_alignment_score
from brew_launch_history import group_and_name
from chips_daily import _institutional_by_date, _streak, main_force_daily, main_force_dates, stored_dates
from daily_bars_store import bar_codes
from database import get_connection, initialize_database
from fundamentals_daily import latest_pe, latest_revenue, tdcc_summary
from heilong_backtest import Bar, _load_bars, _tdcc_weeks, official_score
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

logger = logging.getLogger(__name__)

TW_TZ = timezone(timedelta(hours=8))
LOOKBACK_CALENDAR_DAYS = 420
KEEP_DATES = 3                 # 表裡留幾個交易日
INST_DAYS = 10                 # 法人近 10 日
MF_DAYS = 5                    # 主力 5 日
SCORE_HIST_DAYS = 10           # 均線分數近 10 日走勢
CHIP_HIST_WEEKS = 8            # 集保週增近 8 週
HALF_RANGE_MIN_PCT = 10.0      # 紅半：近三日振幅（最高／最低 −1）至少 10%
HALF_ABOVE_MID_PCT = 1.0       # 紅半：收盤要站在中點之上 1% 以上
NEAR_MA_PCT = 10.0             # 即將穿惡：收盤在月線下但距月線 10% 以內
MAX_CODES = 120
CROSS_MIN_ABOVE_PCT = 2.0      # 穿惡：今收站上今日月線至少 2%
CROSS_MIN_VOLUME = 500         # 穿惡：成交量至少 500 張
TOP_GROUPS = 10                # 族群強度榜前 10 族
TOP_K = 3                      # 族群強度＝七科總分最高的前 3 檔平均
MIN_SUBJECTS = 3               # 至少三科有資料才分強勢／中等／弱勢
PE_HIST_DAYS = 8
REV_HIST_MONTHS = 4
DIAG_BARS = 120                # 問診日線圖給近 120 根
TOP_KEY = "__top__"
CROSS_KEY = "__cross__"
DEFAULT_WEIGHTS = {"fund": 34, "chip": 33, "tech": 33}
OVERALL_LABELS = ((80, "強"), (60, "中上"), (45, "普通"), (30, "偏弱"), (0, "弱"))

RULES = [
    "全部是收盤後算出來的數據，不是即時報價；每個交易日收盤後跟下午報一起更新。基本面是最新一個月的營收（每月 10 號前後公布）、籌碼面是最近一週的集保資料加法人與主力近 5 日、技術面與防守價是最近一個交易日收盤。",
    "技術面＝內定均線分數（收盤站上 6 條均線＋創 6 個天期新高＋多頭排列加分，滿分 15）換算成百分，例如 12 分＝80。日K不足 240 根算不出來。",
    "基本面＝50 分起算：月營收年增 ≥50% +30、≥30% +22、≥15% +14、≥0 +6、≥−15% −8、更差 −18；月增 ≥10% +6、≥0 +2、≥−10% −2、更差 −6；本益比（證交所／櫃買中心公布的實際本益比）<12 +12、<18 +8、<25 +3、<35 0、<50 −6、更高 −12。沒有營收也沒有本益比的不評分。",
    "籌碼面＝50 分起算：集保 400 張以上大戶張數週增％ ×4（上限 ±25）、連續增加每週 +3（上限 +9）；三大法人近 5 日買賣超佔近 5 日成交量的比例（上限 ±15）；主力近 5 日淨買賣佔近 5 日成交量的比例（上限 ±10）。三種資料都沒有的不評分。",
    "綜合＝你勾選的面向加權平均（預設基本面 34％、籌碼面 33％、技術面 33％）；沒資料的面向預設不算進去，不會被當 0 分。≥80 強、≥60 中上、≥45 普通、≥30 偏弱、其餘弱。",
    "防守線：三日低＝不含當天的前三個交易日最低（明日值＝含今天的近三日最低）；月線＝20 日收盤均；紅半＝近三個交易日最高與最低的中間值，只在三日振幅 ≥10% 而且收盤站在中點之上 1% 以上時才列（漲多時多一個防守點）。收盤跌破任一條 → 減碼；月線與三日低雙破 → 出場；即將穿惡＝收盤在月線下但距月線 10% 以內，一根漲停就能站回。價格未還原除權息。",
    "七科小體檢各 0～2 分：均線分數（內定 ≥12 得 2、≥8 得 1）、族群平均分（≥10 得 2、≥7 得 1）、族內名次（依當天漲跌幅，前三分之一得 2、中段得 1）、籌碼（大戶週增 ≥3% 得 2、≥0 得 1）、本益比（<20 得 2、<35 得 1）、營收（年增 ≥30% 得 2、≥0 得 1）、法人（近 5 日與今日都買超得 2、其中一個買超得 1）。🔴＝得 2 分的科目數、🟢＝得 0 分的科目數（本益比不算）；至少三科有資料才分級：有評分科目拿到七成以上＝強勢、四成以上＝中等、其餘弱勢。",
    "族群強度榜：每族取七科總分最高的前 3 檔平均當族群強度，排名前 10 族；今日名單＝這十族裡判定為強勢／中等的股，依族群強度再依七科總分排。收盤排名＝下午報當天族群平均漲跌幅的名次。",
    "今日穿惡＝族群表內、昨收在昨日月線之下、今收站上今日月線 2% 以上、成交量 ≥500 張的股票；站上不到 2% 或量太小的不列。",
    "分數是體質快照，不是買賣訊號。範圍是本站有日K的股票（族群表內加盤中訊號追蹤的全市場股票）。",
]


# ------------------------------------------------------------------ 資料表

def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS checkup_daily (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (trade_date, stock_code)
        )"""
    )


def _r1(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _r2(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _pct(a: float, b: float) -> float | None:
    return round((a / b - 1) * 100, 2) if b else None


def _universe_codes() -> list[str]:
    """有 21 根以上日K的代號（族群表內加盤中訊號追蹤的全市場股票）。"""
    return bar_codes(21)


def _stock_names(codes: list[str]) -> dict[str, str]:
    """股名：族群表優先，其次 stocks 表。"""
    names: dict[str, str] = {}
    initialize_database()
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(f"SELECT stock_code, stock_name FROM stocks WHERE stock_code IN ({','.join('?' for _ in batch)})", batch).fetchall()
            for r in rows:
                if r["stock_name"]:
                    names[str(r["stock_code"]).strip().upper()] = str(r["stock_name"])
    for code in codes:
        group, name = group_and_name(code)
        if name and name != code:
            names[code] = name
    return names


def _pe_history(codes: list[str], limit: int = PE_HIST_DAYS) -> dict[str, list[list[Any]]]:
    """{代號: [[日期, 本益比] 舊到新]}，最近 limit 天。"""
    initialize_database()
    out: dict[str, list[list[Any]]] = {}
    with get_connection() as connection:
        exists = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'stock_pe_daily'").fetchone()
        if not exists:
            return out
        dates = [str(r["trade_date"]) for r in connection.execute("SELECT DISTINCT trade_date FROM stock_pe_daily ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()]
        if not dates:
            return out
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"SELECT trade_date, stock_code, pe FROM stock_pe_daily WHERE trade_date IN ({','.join('?' for _ in dates)}) AND stock_code IN ({','.join('?' for _ in batch)}) ORDER BY trade_date",
                (*dates, *batch),
            ).fetchall()
            for r in rows:
                pe = r["pe"]
                out.setdefault(str(r["stock_code"]).upper(), []).append([str(r["trade_date"]), _r1(pe) if pe is not None and 0 < float(pe) < 500 else None])
    return out


def _revenue_history(codes: list[str], limit: int = REV_HIST_MONTHS) -> dict[str, list[list[Any]]]:
    """{代號: [[年月, 年增％] 舊到新]}，最近 limit 個月。"""
    initialize_database()
    out: dict[str, list[list[Any]]] = {}
    with get_connection() as connection:
        exists = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'stock_revenue_monthly'").fetchone()
        if not exists:
            return out
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"SELECT stock_code, ym, yoy_pct FROM stock_revenue_monthly WHERE stock_code IN ({','.join('?' for _ in batch)}) ORDER BY ym",
                batch,
            ).fetchall()
            for r in rows:
                out.setdefault(str(r["stock_code"]).upper(), []).append([str(r["ym"]), _r1(r["yoy_pct"])])
    return {code: rows[-limit:] for code, rows in out.items()}


def _group_members() -> dict[str, list[str]]:
    return {name: [str(c).strip().upper() for c, _n in members] for name, members in STOCK_GROUPS.items() if name not in SPECIAL_GROUP_NAMES}


# ------------------------------------------------------------------ 分數

def tech_score(official: int | None) -> int | None:
    """技術面：內定均線分數 15 分制換算成百分。"""
    return None if official is None else round(official / 15 * 100)


def fund_score(yoy: float | None, mom: float | None, pe: float | None) -> int | None:
    """基本面：月營收年增／月增＋本益比位階（見 RULES）。"""
    if yoy is None and (pe is None or pe <= 0):
        return None
    s = 50.0
    if yoy is not None:
        s += 30 if yoy >= 50 else 22 if yoy >= 30 else 14 if yoy >= 15 else 6 if yoy >= 0 else -8 if yoy >= -15 else -18
    if mom is not None:
        s += 6 if mom >= 10 else 2 if mom >= 0 else -2 if mom >= -10 else -6
    if pe is not None and pe > 0:
        s += 12 if pe < 12 else 8 if pe < 18 else 3 if pe < 25 else 0 if pe < 35 else -6 if pe < 50 else -12
    return int(max(0, min(100, round(s))))


def chip_score(week_pct: float | None, weeks: int | None, inst5_lots: float | None, mf5_lots: float | None, avg_vol5_lots: float | None) -> int | None:
    """籌碼面：集保大戶週增＋法人 5 日＋主力 5 日（見 RULES）。"""
    if week_pct is None and inst5_lots is None and mf5_lots is None:
        return None
    s = 50.0
    if week_pct is not None:
        s += max(-25.0, min(25.0, week_pct * 4))
        if weeks:
            s += min(9, 3 * int(weeks))
    if avg_vol5_lots and avg_vol5_lots > 0:
        total5 = avg_vol5_lots * 5
        if inst5_lots is not None:
            s += max(-15.0, min(15.0, inst5_lots / total5 * 100))
        if mf5_lots is not None:
            s += max(-10.0, min(10.0, mf5_lots / total5 * 100))
    return int(max(0, min(100, round(s))))


def overall_score(scores: dict[str, int | None], weights: dict[str, float] | None = None, *, zero_missing: bool = False) -> int | None:
    """綜合：有資料的面向加權平均；zero_missing＝沒資料的面向當 0 分。"""
    weights = weights or DEFAULT_WEIGHTS
    total = 0.0
    weight_sum = 0.0
    for key, w in weights.items():
        if not w or w <= 0:
            continue
        value = scores.get(key)
        if value is None and not zero_missing:
            continue
        total += (value or 0) * w
        weight_sum += w
    return None if weight_sum <= 0 else int(round(total / weight_sum))


def overall_label(value: int | None) -> str | None:
    if value is None:
        return None
    for floor, label in OVERALL_LABELS:
        if value >= floor:
            return label
    return "弱"


# ------------------------------------------------------------------ 防守線

def half_line(window: list[Bar], close: float) -> float | None:
    """紅半：近三個交易日最高與最低的中間值；三日振幅 ≥10% 且收盤站在中點之上 1% 以上才用。"""
    if not window:
        return None
    hi = max(b[2] for b in window)
    lo = min(b[3] for b in window)
    if lo <= 0:
        return None
    mid = (hi + lo) / 2
    if (hi / lo - 1) * 100 < HALF_RANGE_MIN_PCT:
        return None
    if close < mid * (1 + HALF_ABOVE_MID_PCT / 100):
        return None
    return round(mid, 2)


def defense_lines(bars: list[Bar]) -> dict[str, Any] | None:
    """bars 舊到新（日期, 開, 高, 低, 收, 量），最後一根是今天；不足 21 根回 None。"""
    if len(bars) < 21:
        return None
    closes = [b[4] for b in bars]
    lows = [b[3] for b in bars]
    close = closes[-1]
    low3_y = min(lows[-4:-1])
    low3_t = min(lows[-3:])
    ma20_y = _mean(closes[-21:-1])
    ma20_t = _mean(closes[-20:])
    half_y = half_line(bars[-4:-1], closes[-2])
    half_t = half_line(bars[-3:], close)
    brk_ma = close < ma20_y
    brk_l3 = close < low3_y
    brk_half = half_y is not None and close < half_y
    near_ma = brk_ma and close >= ma20_y * (1 - NEAR_MA_PCT / 100)
    if brk_ma and brk_l3:
        label = "💥 雙破"
    elif brk_ma:
        label = "🌙 破月線" + ("・🐉 即將穿惡" if near_ma else "")
    elif brk_l3:
        label = "📉 破三日低"
    elif brk_half:
        label = "🔻 破紅半"
    else:
        label = "✅ 守住"
    return {
        "low3Y": low3_y, "low3T": low3_t, "ma20Y": round(ma20_y, 2), "ma20T": round(ma20_t, 2), "halfY": half_y, "halfT": half_t,
        "brkMa": brk_ma, "brkL3": brk_l3, "brkHalf": brk_half, "nearMa": near_ma,
        "dMa": _pct(close, ma20_y), "dL3": _pct(close, low3_y), "dHalf": _pct(close, half_y) if half_y else None,
        "dMaT": _pct(close, ma20_t), "dL3T": _pct(close, low3_t),
        "label": label,
    }


# ------------------------------------------------------------------ 七科

def subjects(official: int | None, group_avg: float | None, group_rank: int | None, group_n: int | None,
             week_pct: float | None, pe: float | None, yoy: float | None, inst5: float | None, inst_today: float | None) -> dict[str, Any]:
    sc: dict[str, int | None] = {
        "ma": None if official is None else 2 if official >= 12 else 1 if official >= 8 else 0,
        "grp": None if group_avg is None else 2 if group_avg >= 10 else 1 if group_avg >= 7 else 0,
        "pos": None if not group_rank or not group_n or group_n < 2 else 2 if group_rank <= -(-group_n // 3) else 1 if group_rank <= -(-2 * group_n // 3) else 0,
        "chip": None if week_pct is None else 2 if week_pct >= 3 else 1 if week_pct >= 0 else 0,
        "pe": None if pe is None or pe <= 0 else 2 if pe < 20 else 1 if pe < 35 else 0,
        "rev": None if yoy is None else 2 if yoy >= 30 else 1 if yoy >= 0 else 0,
        "inst": None if inst5 is None else 2 if inst5 > 0 and (inst_today or 0) > 0 else 1 if inst5 > 0 or (inst_today or 0) > 0 else 0,
    }
    scored = {k: v for k, v in sc.items() if v is not None}
    red = sum(1 for v in scored.values() if v == 2)
    green = sum(1 for k, v in scored.items() if v == 0 and k != "pe")
    total = sum(scored.values())
    possible = 2 * len(scored)
    cls = None if len(scored) < MIN_SUBJECTS else "強勢" if total >= 0.7 * possible else "中等" if total >= 0.4 * possible else "弱勢"
    return {"sc": sc, "red": red, "green": green, "total": total, "possible": possible, "cls": cls}


# ------------------------------------------------------------------ 建表

_state: dict[str, Any] = {"builtAt": None, "lastDate": None, "rows": 0, "lastError": None}
_lock = threading.Lock()
_build_lock = threading.Lock()


def collector_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def evaluate_stock(code: str, bars: list[Bar], latest: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """一檔的健診資料（族群平均、族內名次、七科在 _rebuild 補上）。"""
    closes = [b[4] for b in bars]
    close = closes[-1]
    prev = closes[-2] if len(closes) > 1 else None
    date = bars[-1][0]
    n = len(bars)
    mas = {p: _mean(closes[-p:]) for p in MA_PERIODS if n >= p}
    full = {p: mas[p] for p in MA_PERIODS} if n >= max(MA_PERIODS) else None
    score = ma_alignment_score(full) if full else None
    score2 = official_score(closes, full) if full else None
    hist_scores = []
    if full:
        for i in range(max(0, n - SCORE_HIST_DAYS), n):
            sub = closes[:i + 1]
            hist_scores.append([bars[i][0], official_score(sub) if len(sub) >= max(MA_PERIODS) else None])
    vols = [b[5] for b in bars[-5:]]
    avg_vol5 = _mean([float(v) for v in vols]) if vols else None
    group, _name = group_and_name(code)
    names = ctx["names"]
    pe_row = ctx["pe"].get(code) or {}
    rev_row = ctx["rev"].get(code) or {}
    td = ctx["tdcc"].get(code) or {}
    pe = pe_row.get("pe") if pe_row.get("pe") is not None and 0 < float(pe_row["pe"]) < 500 else None
    inst_dates = ctx["instDates"]
    inst_hist = [[d, (ctx["inst"].get(d, {}).get(code) or {}).get("total")] for d in reversed(inst_dates)]
    totals = [(ctx["inst"].get(d, {}).get(code) or {}).get("total") for d in inst_dates]   # 新到舊
    known5 = [v for v in totals[:5] if v is not None]
    known10 = [v for v in totals if v is not None]
    inst5 = round(sum(known5) / 1000, 1) if known5 else None
    inst10 = round(sum(known10) / 1000, 1) if known10 else None
    today_row = ctx["inst"].get(latest, {}).get(code) if inst_dates and inst_dates[0] == latest else None
    inst_today = round(today_row["total"] / 1000, 1) if today_row else None
    f3 = {k: round(today_row[k] / 1000, 1) for k in ("foreign", "trust", "dealer")} if today_row else None
    inst_streak = _streak(totals) if totals else 0
    mf_nets = [(ctx["mf"].get(d, {}).get(code) or {}).get("net") for d in ctx["mfDates"]]
    known_mf = [v for v in mf_nets if v is not None]
    mf5 = int(sum(known_mf)) if known_mf else None
    mf_streak = _streak(mf_nets) if mf_nets else 0
    weeks = ctx["weeks"].get(code) or []
    chip_hist = []
    for i in range(min(CHIP_HIST_WEEKS, len(weeks) - 1)):
        cur, before = weeks[i], weeks[i + 1]
        chip_hist.append([cur[0], round((cur[1] / before[1] - 1) * 100, 2) if before[1] > 0 else None])
    chip_hist.reverse()
    week_pct = td.get("bigChangePct")
    defense = defense_lines(bars)
    cross = None
    if defense and prev is not None:
        prev_below = prev < defense["ma20Y"]
        cross = {"prevBelow": prev_below, "dist": _pct(close, defense["ma20T"]),
                 "up": prev_below and close >= defense["ma20T"] * (1 + CROSS_MIN_ABOVE_PCT / 100) and bars[-1][5] >= CROSS_MIN_VOLUME}
    fund = fund_score(rev_row.get("yoy"), rev_row.get("mom"), pe)
    chip = chip_score(week_pct, td.get("weeks"), inst5, mf5, avg_vol5)
    tech = tech_score(score2)
    overall = overall_score({"fund": fund, "chip": chip, "tech": tech})
    return {
        "code": code, "name": names.get(code, code), "group": group, "date": date, "stale": date != latest,
        "close": close, "open": bars[-1][1], "high": bars[-1][2], "low": bars[-1][3], "prev": prev, "volume": bars[-1][5],
        "chgPct": _pct(close, prev) if prev else None,
        "chg20Pct": _pct(close, closes[-21]) if n >= 21 else None,
        "distMa20Pct": _pct(close, mas[20]) if 20 in mas else None,
        "ma": {str(p): round(v, 2) for p, v in mas.items()},
        "score": score, "score2": score2,
        "scores": {"fund": fund, "chip": chip, "tech": tech, "overall": overall, "overallLabel": overall_label(overall)},
        "fund": {"yoy": _r1(rev_row.get("yoy")), "mom": _r1(rev_row.get("mom")), "ym": rev_row.get("ym"), "revenue": rev_row.get("revenue"),
                 "pe": _r1(pe), "peDate": pe_row.get("date")},
        "chip": {"weekPct": week_pct, "weeks": td.get("weeks"), "bigPct": td.get("bigPct"), "tdccDate": td.get("date"),
                 "inst5": inst5, "inst10": inst10, "instToday": inst_today, "instStreak": inst_streak, "f3": f3,
                 "mf5": mf5, "mfStreak": mf_streak, "avgVol5": _r1(avg_vol5)},
        "def": defense,
        "cross": cross,
        "hist": {"score10": hist_scores, "inst10": inst_hist, "chip8": chip_hist,
                 "pe8": ctx.get("peHist", {}).get(code, []), "rev4": ctx.get("revHist", {}).get(code, [])},
        "groupAvg2": None, "groupRank": None, "groupN": None,
        "subjects": None,
        "disposed": code in ctx["disposed"],
    }


def rebuild(*, force: bool = False) -> dict[str, Any]:
    with _build_lock:
        try:
            result = _rebuild()
            with _lock:
                _state.update({"builtAt": datetime.now(TW_TZ).isoformat(timespec="seconds"), "lastDate": result.get("date"),
                               "rows": result.get("rows", 0), "lastError": None})
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("checkup rebuild failed")
            with _lock:
                _state["lastError"] = str(exc)
            raise


def _rebuild() -> dict[str, Any]:
    initialize_database()
    latest = _latest_bar_date()
    if not latest:
        return {"date": None, "rows": 0}
    codes = _universe_codes()
    since = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    bars_by_code = _load_bars(codes, since=since, until=latest)
    inst_dates = [d for d in stored_dates(30) if d <= latest][:INST_DAYS]
    mf_dates = [d for d in main_force_dates(30) if d <= latest][:MF_DAYS]
    try:
        from disposition_stocks import get_disposition_map

        disposed = {str(c).strip().upper() for c in get_disposition_map().keys()}
    except Exception:  # noqa: BLE001
        disposed = set()
    ctx = {
        "names": _stock_names(codes),
        "pe": latest_pe(codes), "rev": latest_revenue(codes), "tdcc": tdcc_summary(codes, weeks=CHIP_HIST_WEEKS + 1),
        "weeks": _tdcc_weeks(codes), "peHist": _pe_history(codes), "revHist": _revenue_history(codes),
        "instDates": inst_dates, "inst": _institutional_by_date(inst_dates, codes) if inst_dates else {},
        "mfDates": mf_dates, "mf": {d: main_force_daily(d, codes) for d in mf_dates},
        "disposed": disposed,
    }
    rows: dict[str, dict[str, Any]] = {}
    for code, bars in bars_by_code.items():
        if len(bars) < 21:
            continue
        rows[code] = evaluate_stock(code, bars, latest, ctx)
    # 族群平均（內定）與族內名次（當天漲跌幅）
    for name, member_codes in _group_members().items():
        fresh = [rows[c] for c in member_codes if c in rows and not rows[c]["stale"]]
        scores = [r["score2"] for r in fresh if r["score2"] is not None]
        avg = round(_mean(scores), 1) if scores else None
        ranked = sorted(fresh, key=lambda r: -(r["chgPct"] or 0))
        for i, r in enumerate(ranked):
            if r["group"] == name:
                r["groupAvg2"], r["groupRank"], r["groupN"] = avg, i + 1, len(ranked)
    for r in rows.values():
        r["subjects"] = subjects(r["score2"], r["groupAvg2"], r["groupRank"], r["groupN"], r["chip"]["weekPct"], r["fund"]["pe"], r["fund"]["yoy"],
                                 r["chip"]["inst5"], r["chip"]["instToday"])
    top = build_top(rows, latest)
    for r in rows.values():
        g = top["byGroup"].get(r["group"])
        r["groupSrank"], r["groupStrength"], r["groupCloseRank"] = (g["srank"], g["strength"], g.get("rank")) if g else (None, None, None)
    cross = build_cross(rows, latest)
    with get_connection() as connection:
        _schema(connection)
        connection.execute("DELETE FROM checkup_daily WHERE trade_date = ?", (latest,))
        connection.executemany("INSERT INTO checkup_daily (trade_date, stock_code, payload) VALUES (?, ?, ?)",
                               [(latest, code, json.dumps(r, ensure_ascii=False, separators=(",", ":"))) for code, r in rows.items()] +
                               [(latest, TOP_KEY, json.dumps({k: v for k, v in top.items() if k != "byGroup"}, ensure_ascii=False, separators=(",", ":"))),
                                (latest, CROSS_KEY, json.dumps(cross, ensure_ascii=False, separators=(",", ":")))])
        connection.execute(
            "DELETE FROM checkup_daily WHERE trade_date NOT IN (SELECT DISTINCT trade_date FROM checkup_daily ORDER BY trade_date DESC LIMIT ?)",
            (KEEP_DATES,),
        )
    return {"date": latest, "rows": len(rows), "stocks": len(codes), "instDates": inst_dates, "mfDates": mf_dates,
            "topGroups": len(top["groups"]), "todayList": len(top["list"]), "cross": cross["n"]}


def compact(r: dict[str, Any]) -> dict[str, Any]:
    """名單用的精簡列（同族對照、今日名單、穿惡）。"""
    sub = r.get("subjects") or {}
    d = r.get("def") or {}
    return {
        "code": r["code"], "name": r["name"], "group": r["group"], "cls": sub.get("cls"), "total": sub.get("total"), "red": sub.get("red"), "green": sub.get("green"),
        "chgPct": r["chgPct"], "close": r["close"], "prev": r.get("prev"), "volume": r.get("volume"), "score2": r["score2"], "weekPct": r["chip"]["weekPct"],
        "pe": r["fund"]["pe"], "yoy": r["fund"]["yoy"], "inst5": r["chip"]["inst5"], "scores": r["scores"],
        "def": {k: d.get(k) for k in ("low3T", "ma20T", "halfT", "brkMa", "brkL3", "brkHalf", "nearMa", "label")} if d else None,
        "cross": r.get("cross"), "groupSrank": r.get("groupSrank"), "disposed": r.get("disposed"),
    }


def build_top(rows: dict[str, dict[str, Any]], latest: str) -> dict[str, Any]:
    """族群強度榜（每族七科總分前 3 檔平均）與今日名單（前 10 族的強勢／中等）。"""
    close_rank: dict[str, int] = {}
    try:
        from swing_report import load_report

        report = load_report(latest) or {}
        close_rank = {g["name"]: g["rank"] for g in report.get("groups", []) if g.get("name") and g.get("rank")}
    except Exception:  # noqa: BLE001
        close_rank = {}
    groups: list[dict[str, Any]] = []
    for name, member_codes in _group_members().items():
        fresh = [rows[c] for c in member_codes if c in rows and not rows[c]["stale"] and rows[c].get("subjects") and rows[c]["subjects"].get("cls")]
        if not fresh:
            continue
        ranked = sorted(fresh, key=lambda r: (-r["subjects"]["total"], -(r["chgPct"] or 0)))
        k = min(TOP_K, len(ranked))
        totals = [r["subjects"]["total"] for r in ranked]
        cnt = {"強勢": 0, "中等": 0, "弱勢": 0}
        for r in fresh:
            cnt[r["subjects"]["cls"]] = cnt.get(r["subjects"]["cls"], 0) + 1
        groups.append({"g": name, "n": len(fresh), "k": k, "strength": round(_mean(totals[:k]), 2), "avgAll": round(_mean(totals), 2),
                       "top": [[r["code"], r["name"], r["subjects"]["total"], r["subjects"]["cls"]] for r in ranked[:k]], "cnt": cnt, "rank": close_rank.get(name)})
    groups.sort(key=lambda g: (-g["strength"], -g["avgAll"], g["g"]))
    for i, g in enumerate(groups):
        g["srank"] = i + 1
    by_group = {g["g"]: g for g in groups}
    listing: list[dict[str, Any]] = []
    members = _group_members()
    for g in groups[:TOP_GROUPS]:
        picks = [rows[c] for c in members[g["g"]] if c in rows and not rows[c]["stale"] and rows[c].get("subjects") and rows[c]["subjects"].get("cls") in ("強勢", "中等")]
        picks.sort(key=lambda r: (-r["subjects"]["total"], -(r["chgPct"] or 0)))
        for r in picks:
            item = compact(r)
            item["srank"], item["gstr"] = g["srank"], g["strength"]
            listing.append(item)
    return {"date": latest, "groups": groups, "list": listing, "byGroup": by_group}


def build_cross(rows: dict[str, dict[str, Any]], latest: str) -> dict[str, Any]:
    """今日穿惡：族群表內、昨收在月線下、今收站上月線 2% 以上且量夠。"""
    picks = [r for r in rows.values() if r["group"] and not r["stale"] and r.get("cross") and r["cross"]["up"]]
    picks.sort(key=lambda r: -(r["chgPct"] or 0))
    near = sum(1 for r in rows.values() if r["group"] and not r["stale"] and r.get("def") and r["def"]["brkMa"] and r["def"]["nearMa"])
    groups = {"強勢": [], "中等": [], "弱勢": []}
    for r in picks:
        cls = (r.get("subjects") or {}).get("cls") or "弱勢"
        groups.setdefault(cls, []).append(compact(r))
    return {"date": latest, "n": len(picks), "nearLeft": near, "groups": groups}


# ------------------------------------------------------------------ 查詢

def latest_date() -> str | None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT MAX(trade_date) AS d FROM checkup_daily").fetchone()
    return str(row["d"]) if row and row["d"] else None


def load_rows(trade_date: str, codes: list[str] | None = None) -> dict[str, dict[str, Any]]:
    initialize_database()
    out: dict[str, dict[str, Any]] = {}
    with get_connection() as connection:
        _schema(connection)
        if codes is None:
            rows = connection.execute("SELECT stock_code, payload FROM checkup_daily WHERE trade_date = ?", (trade_date,)).fetchall()
        else:
            rows = []
            for start in range(0, len(codes), 400):
                batch = codes[start:start + 400]
                rows += connection.execute(
                    f"SELECT stock_code, payload FROM checkup_daily WHERE trade_date = ? AND stock_code IN ({','.join('?' for _ in batch)})",
                    (trade_date, *batch),
                ).fetchall()
    for r in rows:
        code = str(r["stock_code"])
        if code in (TOP_KEY, CROSS_KEY):
            continue
        out[code] = json.loads(r["payload"])
    return out


def load_special(trade_date: str, key: str) -> dict[str, Any] | None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT payload FROM checkup_daily WHERE trade_date = ? AND stock_code = ?", (trade_date, key)).fetchone()
    return json.loads(row["payload"]) if row else None


def diag(code: str) -> dict[str, Any]:
    """個股問診：那檔的完整資料＋近 120 根日K＋同族對照＋族群強度榜／今日名單＋穿惡名單。"""
    code = str(code or "").strip().upper()
    date = latest_date()
    if not date:
        return {"status": "empty", "reason": "還沒有健診資料（收盤後會自動建）", "code": code, "rules": RULES, "collector": collector_status()}
    if not code:
        return {"status": "empty", "reason": "請輸入股號", "code": code, "date": date, "rules": RULES, "collector": collector_status()}
    stock = load_rows(date, [code]).get(code)
    if not stock:
        return {"status": "missing", "reason": "本站沒有這檔的日K", "code": code, "date": date, "rules": RULES, "collector": collector_status()}
    since = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=DIAG_BARS * 2)).strftime("%Y-%m-%d")
    bars = _load_bars([code], since=since, until=date).get(code, [])[-DIAG_BARS:]
    siblings: list[dict[str, Any]] = []
    if stock["group"]:
        member_codes = _group_members().get(stock["group"], [])
        member_rows = load_rows(date, member_codes)
        siblings = [compact(r) for r in member_rows.values()]
        siblings.sort(key=lambda r: (-(r["total"] or -1), -(r["chgPct"] or 0)))
    top = load_special(date, TOP_KEY) or {"groups": [], "list": []}
    group_info = next((g for g in top.get("groups", []) if g["g"] == stock["group"]), None)
    return {
        "status": "ok", "date": date, "code": code, "stock": stock, "bars": [list(b) for b in bars], "siblings": siblings, "groupInfo": group_info,
        "top": top, "cross": load_special(date, CROSS_KEY) or {"n": 0, "groups": {}},
        "rules": RULES, "collector": collector_status(),
    }


def normalize_codes(raw: str | list[str] | None) -> list[str]:
    """『2481/2408, 2344 2330』這樣的字串或清單 → 去重的代號清單（最多 MAX_CODES 檔）。"""
    if raw is None:
        return []
    text = raw if isinstance(raw, str) else " ".join(str(x) for x in raw)
    out: list[str] = []
    for token in text.replace("/", " ").replace(",", " ").replace("，", " ").replace("、", " ").replace("\n", " ").split():
        code = token.strip().upper()
        if not code or not any(ch.isdigit() for ch in code):
            continue
        if code not in out:
            out.append(code)
    return out[:MAX_CODES]


def _hi_lens(codes: list[str], trade_date: str) -> dict[str, int]:
    """創高天數（今天收盤是近幾日最高收盤）：創高黑選股的特徵表 heilong_daily 就有，回應時順便帶上
    （2026-10-05 使用者：自選股盤後籌碼要看）。用不晚於健診日期的最新一天；表還沒建就不給。"""
    if not codes:
        return {}
    out: dict[str, int] = {}
    try:
        with get_connection() as connection:
            row = connection.execute("SELECT MAX(trade_date) AS d FROM heilong_daily WHERE trade_date <= ?", (trade_date,)).fetchone()
            day = row["d"] if row else None
            if not day:
                return {}
            for start in range(0, len(codes), 400):
                batch = codes[start:start + 400]
                for r in connection.execute(
                    f"SELECT stock_code, hi_len FROM heilong_daily WHERE trade_date = ? AND hi_len IS NOT NULL "
                    f"AND stock_code IN ({','.join('?' for _ in batch)})",
                    (day, *batch),
                ):
                    out[str(r["stock_code"])] = int(r["hi_len"])
    except sqlite3.Error:
        return {}
    return out


def checkup(codes: str | list[str] | None) -> dict[str, Any]:
    wanted = normalize_codes(codes)
    date = latest_date()
    if not date:
        return {"status": "empty", "reason": "還沒有健診資料（收盤後會自動建）", "codes": wanted, "rules": RULES, "collector": collector_status()}
    rows = load_rows(date, wanted) if wanted else {}
    hi = _hi_lens([c for c in wanted if c in rows], date)
    return {
        "status": "ok", "date": date, "codes": wanted,
        "rows": [{**rows[c], "hiLen": hi.get(c)} for c in wanted if c in rows],
        "missing": [c for c in wanted if c not in rows],
        "weights": DEFAULT_WEIGHTS, "rules": RULES, "collector": collector_status(),
    }
