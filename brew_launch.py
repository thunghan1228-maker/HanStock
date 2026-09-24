"""醞釀／發動選股（2026-09-24 使用者，照老師的「1＝醞釀（整理形態）、2＝發動（突破）」）。

醞釀（1）以上一個交易日收盤為準，下面全部符合：
  - 均線分數 ≥ 10：5/10/20/60/120/240 日線兩兩比較共 15 組，短天期在長天期上面得 1 分
    （跟 ma_alignment_score.py 同一個算法，老師說的「均線分數」就是這個，使用者確認過）
  - 收盤站在月線（20 日線）上
  - 近 10 個交易日在箱子裡整理：箱頂（最高價）到箱底（最低價）相差 ≤ 20%
    （原本 15%，老師舉的醞釀例子 6213／2464／3455 箱子是 18～19%，使用者 2026-09-24 同意放寬到 20%）
  - 5／10／20 日線糾結：三條最高到最低相差 ≤ 4%
發動（2）要用即時價量，在前端判斷；這裡提供需要的數字（箱頂、均線部分和、5 日均量、發行張數）：
  - 價格衝過箱頂（近 10 個交易日最高價）＝過高
  - 均線分數 > 10（用即時價當今天收盤重算）
  - 周轉高：今天預估周轉率 ≥ 5%，或預估量 ≥ 5 日均量 1.5 倍

資料來源是官方日K（bars_1d）跟處置股預測收集的市值（stock_fundamentals_daily，市值÷收盤＝發行股數）；
只看 43 個一般族群的成員（股期標的不是產業族群）。日K不足 240 天（MA240 算不出來）或日K沒跟上
（例如停牌）的個股不列入，另外列在 insufficient／stale 讓人知道漏了誰。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_connection, initialize_database
from disposition_fundamentals_store import ensure_fundamentals_schema
from stock_groups import STOCK_GROUPS

TW_TZ = timezone(timedelta(hours=8))
EXCLUDED_GROUPS = {"股期標的"}
MA_PERIODS = (5, 10, 20, 60, 120, 240)
BOX_DAYS = 10
BOX_RANGE_MAX_PCT = 20.0
MA_SPREAD_MAX_PCT = 4.0
BREW_MIN_SCORE = 10
LAUNCH_MIN_SCORE = 11
TURNOVER_MIN_PCT = 5.0
VOLUME_RATIO_MIN = 1.5
AVG_VOLUME_DAYS = 5
# MA240 要 240 根；日曆天抓寬一點（一年約 245 個交易日），交易日數不夠的個股會被判 insufficient。
LOOKBACK_CALENDAR_DAYS = 420
FUNDAMENTALS_LOOKBACK_DAYS = 30
CACHE_SECONDS = 1800

RULES = {
    "boxDays": BOX_DAYS, "boxRangeMaxPct": BOX_RANGE_MAX_PCT, "maSpreadMaxPct": MA_SPREAD_MAX_PCT,
    "brewMinScore": BREW_MIN_SCORE, "launchMinScore": LAUNCH_MIN_SCORE,
    "turnoverMinPct": TURNOVER_MIN_PCT, "volumeRatioMin": VOLUME_RATIO_MIN, "avgVolumeDays": AVG_VOLUME_DAYS,
    "maPeriods": list(MA_PERIODS),
}

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "value": None}


def _latest_bar_date() -> str | None:
    initialize_database()
    with get_connection() as connection:
        row = connection.execute("SELECT MAX(substr(bar_time, 1, 10)) AS d FROM bars_1d").fetchone()
    return str(row["d"]) if row and row["d"] else None


def session_date(now: datetime | None = None) -> str:
    """今天要判斷的那個交易日：平日就是今天（盤前看昨天收盤後的醞釀、盤中／收盤後即時價判斷發動）；
    週末沒開盤，即時報價停在最後一個交易日，箱子也要以那天「之前」的日K為準，才不會把那天自己算進箱子。"""
    now = now or datetime.now(TW_TZ)
    today = now.strftime("%Y-%m-%d")
    if now.weekday() < 5:
        return today
    latest = _latest_bar_date()
    return latest if latest and latest <= today else today


def group_codes() -> list[str]:
    return sorted({
        str(code).strip().upper()
        for name, members in STOCK_GROUPS.items() if name not in EXCLUDED_GROUPS
        for code, _stock_name in members
    })


def _load_bars(codes: list[str], *, session: str) -> dict[str, list[tuple[str, float, float, float, int]]]:
    """{代號: [(日期, 高, 低, 收, 量張), ...舊到新]}，只取 session 之前的日K。"""
    initialize_database()
    since = (datetime.strptime(session, "%Y-%m-%d") - timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    out: dict[str, list[tuple[str, float, float, float, int]]] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"""
                SELECT stock_code, substr(bar_time, 1, 10) AS d, high, low, close, volume FROM bars_1d
                WHERE stock_code IN ({placeholders}) AND substr(bar_time, 1, 10) < ? AND substr(bar_time, 1, 10) >= ?
                """,
                (*batch, session, since),
            ).fetchall()
            for row in rows:
                out.setdefault(str(row["stock_code"]).strip().upper(), []).append(
                    (str(row["d"]), float(row["high"]), float(row["low"]), float(row["close"]), int(row["volume"] or 0))
                )
    for bars in out.values():
        bars.sort(key=lambda bar: bar[0])
    return out


def _load_market_values(codes: list[str], *, session: str) -> dict[str, tuple[str, float]]:
    """{代號: (日期, 市值元)}，取 session 之前最近一筆有市值的資料。"""
    ensure_fundamentals_schema()
    since = (datetime.strptime(session, "%Y-%m-%d") - timedelta(days=FUNDAMENTALS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    out: dict[str, tuple[str, float]] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"""
                SELECT stock_code, trade_date, market_value FROM stock_fundamentals_daily
                WHERE stock_code IN ({placeholders}) AND trade_date < ? AND trade_date >= ?
                  AND market_value IS NOT NULL AND market_value > 0
                """,
                (*batch, session, since),
            ).fetchall()
            for row in rows:
                code = str(row["stock_code"]).strip().upper()
                day = str(row["trade_date"])[:10]
                if code not in out or day > out[code][0]:
                    out[code] = (day, float(row["market_value"]))
    return out


def ma_alignment_score(mas: dict[int, float]) -> int:
    periods = sorted(mas)
    return sum(1 for i, short in enumerate(periods) for long in periods[i + 1:] if mas[short] > mas[long])


def analyze_stock(bars: list[tuple[str, float, float, float, int]]) -> dict[str, Any] | None:
    """bars 舊到新、全部在 session 之前；日K不足 240 根回 None。"""
    if len(bars) < max(MA_PERIODS):
        return None
    closes = [bar[3] for bar in bars]
    mas = {period: sum(closes[-period:]) / period for period in MA_PERIODS}
    score = ma_alignment_score(mas)
    box = bars[-BOX_DAYS:]
    box_high = max(bar[1] for bar in box)
    box_low = min(bar[2] for bar in box)
    box_range_pct = (box_high / box_low - 1) * 100 if box_low > 0 else None
    short = [mas[5], mas[10], mas[20]]
    ma_spread_pct = (max(short) / min(short) - 1) * 100 if min(short) > 0 else None
    prev_close = closes[-1]
    volumes = [bar[4] for bar in bars[-AVG_VOLUME_DAYS:]]
    brewing = (
        score >= BREW_MIN_SCORE
        and prev_close >= mas[20]
        and box_range_pct is not None and box_range_pct <= BOX_RANGE_MAX_PCT
        and ma_spread_pct is not None and ma_spread_pct <= MA_SPREAD_MAX_PCT
    )
    return {
        "asOf": bars[-1][0],
        "prevClose": prev_close,
        "boxHigh": box_high,
        "boxLow": box_low,
        "boxRangePct": round(box_range_pct, 2) if box_range_pct is not None else None,
        "ma": {str(period): round(value, 4) for period, value in mas.items()},
        # 前端即時算今天的均線：MA_p(今天) = (最近 p-1 根收盤合計 + 即時價) / p
        "maSums": {str(period): round(sum(closes[-(period - 1):]), 4) for period in MA_PERIODS},
        "maSpreadPct": round(ma_spread_pct, 2) if ma_spread_pct is not None else None,
        "score": score,
        "avgVol5": round(sum(volumes) / len(volumes), 1) if volumes else None,
        "brewing": brewing,
    }


def compute_brew_launch(*, session: str | None = None) -> dict[str, Any]:
    session = session or session_date()
    codes = group_codes()
    bars_by_code = _load_bars(codes, session=session)
    market_values = _load_market_values(codes, session=session)
    as_of = max((bars[-1][0] for bars in bars_by_code.values() if bars), default=None)
    stocks: dict[str, Any] = {}
    insufficient: list[str] = []
    stale: list[str] = []
    for code in codes:
        bars = bars_by_code.get(code) or []
        if not bars:
            insufficient.append(code)
            continue
        if as_of and bars[-1][0] != as_of:
            stale.append(code)  # 最後一根日K比全市場舊：停牌或日K沒跟上，不能拿舊資料當昨天
            continue
        info = analyze_stock(bars)
        if info is None:
            insufficient.append(code)
            continue
        shares_lots = None
        mv = market_values.get(code)
        if mv:
            close_that_day = next((bar[3] for bar in reversed(bars) if bar[0] == mv[0]), info["prevClose"])
            if close_that_day > 0:
                shares_lots = round(mv[1] / close_that_day / 1000, 1)
        info["sharesLots"] = shares_lots
        stocks[code] = info
    return {
        "status": "ok",
        "session": session,
        "asOf": as_of,
        "rules": RULES,
        "stockCount": len(stocks),
        "brewingCount": sum(1 for info in stocks.values() if info["brewing"]),
        "insufficient": insufficient,
        "stale": stale,
        "stocks": stocks,
        "generatedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"),
    }


def clear_cache() -> None:
    with _cache_lock:
        _cache.update({"key": None, "at": 0.0, "value": None})


def _history_backfill_status() -> dict[str, Any] | None:
    try:
        from daily_bars_history_backfill import backfill_state

        state = backfill_state()
    except Exception:  # noqa: BLE001
        return None
    result = state.get("result") or {}
    return {"done": state["done"], "progress": state.get("progress"), "updatedAt": state.get("updatedAt"),
            "source": result.get("source"), "insertedBars": result.get("insertedBars"),
            "failures": result.get("failureCount", len(result.get("failures") or [])),
            "mismatchCount": result.get("mismatchCount"), "stillShortCount": result.get("stillShortCount")}


def get_brew_launch() -> dict[str, Any]:
    """快取半小時：只用到收盤後才會變的日K跟市值；即時價量由前端自己套。日K歷史回補的進度每次即時附上
    （回補還沒完成時多數個股 MA240 算不出來，前端要能說明為什麼清單是空的）。"""
    session = session_date()
    now = time.monotonic()
    with _cache_lock:
        cached = _cache["value"] if _cache["key"] == session and _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS else None
    if cached is None:
        cached = compute_brew_launch(session=session)
        with _cache_lock:
            _cache.update({"key": session, "at": now, "value": cached})
    return dict(cached, historyBackfill=_history_backfill_status())
