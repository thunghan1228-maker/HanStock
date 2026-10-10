"""創高黑龍「今日暫定名單（12:00）」（2026-10-10 使用者：照莊爸創高黑龍・績效分析的「⏱ 今日暫定名單（12:00）」做）。

交易日 12:00 用證交所 MIS 即時報價（飆股雷達同一支 fetch_live_bars）組今天到現在的K棒，接在官方日K後面，
算出跟收盤整表一樣的欄位（均線分數、K棒、漲跌幅、近 20 日漲逾 8% 次數、5 日均成交值、月季乖離、族群平均分、
集保週籌碼、處置），存成一份「今天 12:00 的整表」。頁面用跟收盤名單同一組參數篩（heilong_backtest.select_rows），
只給名單與條件；收盤後官方日K進來、整表重算，就以正式的今日名單為準。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database
from trading_days import is_trading_day, previous_trading_day

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))
NOON_AT = (12, 0)            # 12:00 算
NOON_GRACE_MINUTES = 20      # 12:20 以後就不補了（報價已經不是 12:00 的）
POLL_SECONDS = 30
MIN_QUOTES = 300             # 即時報價太少（被擋、休市）不算

_state: dict[str, Any] = {"lastRun": None, "lastError": None, "running": False}
_lock = threading.Lock()


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS heilong_noon (
            trade_date TEXT PRIMARY KEY, computed_at TEXT NOT NULL, quotes INTEGER NOT NULL, rows_json TEXT NOT NULL
        )"""
    )


def _now() -> datetime:
    return datetime.now(TW_TZ)


def build_rows(day: str, live: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """官方日K（到前一個交易日，還原過）＋今天的即時K棒 → {代號: 列}，欄位跟 heilong_backtest.load_rows 一樣。"""
    from heilong_backtest import (
        DISPOSITION_LOOKBACK_DAYS, HISTORY_DAYS, LOOKBACK_CALENDAR_DAYS, _bar_dates, _disposed, _disposition_spans,
        _group_members, _load_bars, _mean, _out_days, _tdcc_weeks, _week_pct, compute_features,
    )
    from brew_launch import group_codes, skipped_codes
    from brew_launch_history import group_and_name

    codes = sorted(live)
    since = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    yesterday = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    skipped = skipped_codes()
    group_set = {c for c in group_codes() if c not in skipped}
    spans = _disposition_spans((datetime.strptime(day, "%Y-%m-%d") - timedelta(days=DISPOSITION_LOOKBACK_DAYS)).strftime("%Y-%m-%d"))
    trading_dates = sorted(set(_bar_dates(HISTORY_DAYS + 70)) | {day})
    weeks = _tdcc_weeks(codes)
    rows: dict[str, dict[str, Any]] = {}
    for start in range(0, len(codes), 150):
        batch = codes[start:start + 150]
        for code, bars in _load_bars(batch, since=since, until=yesterday, adjusted=True).items():
            q = live[code]
            bars = bars + [(day, float(q["open"]), float(q["high"]), float(q["low"]), float(q["close"]), int(round(q["volume"] or 0)))]
            f = compute_features(bars, {day}).get(day)
            if not f:
                continue
            group, _name = group_and_name(code)
            week_pct, week_date = _week_pct(weeks.get(code, []), day)
            disposed = _disposed(spans, code, day)
            rows[code] = {
                "code": code, "date": day, "open": f["open"], "high": f["high"], "low": f["low"], "close": f["close"], "volume": f["volume"],
                "prevClose": f["prevClose"], "changePct": f["changePct"], "score": f["score"], "hits20": f["hits20"], "val5": f["val5"],
                "group": group, "groupAvg": None, "weekPct": week_pct, "weekDate": week_date, "disposed": disposed,
                "score2": f["score2"], "groupAvg2": None, "bias": f["bias"],
                "outDays": None if disposed else _out_days(spans, code, day, trading_dates),
                "attention": False,       # 今天的注意股收盤後才公布
                "inGroup": code in group_set, "hiLen": f["hiLen"],
                "maBits": f["maBits"], "hiBits": f["hiBits"], "align": f["align"],
            }
    # 族群平均分：用今天 12:00 的分數重算
    for name, members in _group_members().items():
        s1 = [rows[c]["score"] for c in members if c in rows and rows[c]["score"] is not None]
        s2 = [rows[c]["score2"] for c in members if c in rows and rows[c]["score2"] is not None]
        avg1 = round(_mean(s1), 1) if s1 else None
        avg2 = round(_mean(s2), 1) if s2 else None
        for c in members:
            if c in rows and rows[c]["group"] == name:
                rows[c]["groupAvg"], rows[c]["groupAvg2"] = avg1, avg2
    return rows


def run_noon(*, now: datetime | None = None, fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """抓即時報價、算 12:00 整表、存起來。"""
    from grail_radar import _universe, fetch_live_bars

    now = now or _now()
    day = now.date().isoformat()
    universe = _universe()
    live = fetch_live_bars(universe, fetcher=fetcher)
    live = {code: bar for code, bar in live.items() if bar.get("date") == day}
    if len(live) < min(MIN_QUOTES, max(1, len(universe) // 2)):
        return {"date": day, "skipped": f"今天的即時報價只有 {len(live)} 檔，不算"}
    rows = build_rows(day, live)
    computed_at = _now().isoformat(timespec="seconds")
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.execute(
            """INSERT INTO heilong_noon (trade_date, computed_at, quotes, rows_json) VALUES (?, ?, ?, ?)
               ON CONFLICT(trade_date) DO UPDATE SET computed_at = excluded.computed_at, quotes = excluded.quotes, rows_json = excluded.rows_json""",
            (day, computed_at, len(live), json.dumps(list(rows.values()), ensure_ascii=False, separators=(",", ":"))),
        )
    return {"date": day, "quotes": len(live), "rows": len(rows), "at": computed_at}


def load_noon(day: str | None = None) -> dict[str, Any] | None:
    """{date, at, quotes, rows: {代號: 列}}；day 沒給就拿最新一份。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        if day:
            row = connection.execute("SELECT * FROM heilong_noon WHERE trade_date = ?", (day,)).fetchone()
        else:
            row = connection.execute("SELECT * FROM heilong_noon ORDER BY trade_date DESC LIMIT 1").fetchone()
    if not row:
        return None
    rows = {r["code"]: r for r in json.loads(row["rows_json"] or "[]")}
    return {"date": str(row["trade_date"]), "at": str(row["computed_at"]), "quotes": int(row["quotes"]), "rows": rows}


def noon_section(params: dict[str, Any], latest_official: str | None) -> dict[str, Any] | None:
    """給創高黑龍頁面：比收盤整表新的那份 12:00 名單（收盤整表進來了就不給，以正式名單為準）。"""
    from heilong_backtest import select_rows

    noon = load_noon()
    if not noon or (latest_official and noon["date"] <= latest_official):
        return None
    picked = select_rows(noon["rows"], params)
    from brew_launch_history import group_and_name

    out = []
    for r in picked:
        _g, name = group_and_name(r["code"])
        out.append({"code": r["code"], "name": name, "group": r["group"], "price": r["close"], "changePct": r["changePct"],
                    "score": r["score"], "score2": r["score2"], "groupAvg": r["groupAvg"], "groupAvg2": r["groupAvg2"],
                    "weekPct": r["weekPct"], "hits20": r["hits20"], "val5": r["val5"], "bias": r["bias"], "volume": r["volume"],
                    "k": "black" if r["close"] < r["open"] else ("red" if r["close"] > r["open"] else "flat")})
    return {"date": noon["date"], "at": noon["at"][11:16], "quotes": noon["quotes"], "count": len(out), "rows": out}


# ------------------------------------------------------------------ 排程

def due(now: datetime, done_day: str | None) -> bool:
    """交易日 12:00～12:20 之間、今天還沒算過。"""
    if not is_trading_day(now) or done_day == now.date().isoformat():
        return False
    minutes = now.hour * 60 + now.minute
    start = NOON_AT[0] * 60 + NOON_AT[1]
    return start <= minutes <= start + NOON_GRACE_MINUTES


def _loop() -> None:
    done_day: str | None = None
    while True:
        now = _now()
        if done_day is None:
            existing = load_noon(now.date().isoformat())
            done_day = existing["date"] if existing else previous_trading_day(now.date()).isoformat()
        if due(now, done_day):
            try:
                result = run_noon(now=now)
                with _lock:
                    _state.update({"lastRun": result, "lastError": None})
                if not result.get("skipped"):
                    done_day = result["date"]
            except Exception as exc:  # noqa: BLE001
                logger.exception("heilong noon failed")
                with _lock:
                    _state["lastError"] = f"{type(exc).__name__}: {exc}"[:300]
        time.sleep(POLL_SECONDS)


def start_heilong_noon_collector() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_loop, name="heilong-noon", daemon=True).start()
    return True


def collector_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)
