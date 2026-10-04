"""日K補到三年（創高黑選股要用；2026-10-04 使用者回「做」）。

原本日K只回補一年（2025-08 起），均線分數要 240 根才算得出來，黑龍回測的「近 60 天」實際只有 8/17 以後
三十幾天有名單。這裡把全市場日K補到 HISTORY_YEARS 年前：
- 上市：正式站主機直接抓證交所 MI_INDEX（一天一份全市場），已經收齊的日子不再抓，休市日記下來不再問。
- 上櫃：櫃買中心擋正式站主機，走 tw-groups data 分支的鏡像（tpex/quotes/YYYY-MM.json.gz，排程主機每天 16:50 更新，
  手動跑可以一次回補幾年），只補資料庫裡還沒有的日子。
- 還原事件（price_adjust）：證交所減資／變更面額表（直接抓）、櫃買減資表（鏡像）、櫃買行情的漲跌反推恢復買賣參考價、
  最後用日K推測其他停止買賣後跳空超過一成的（例如 ETF 分割）。
補完會叫黑龍表重算（還原後的價格、更長的歷史）。已經有的日K一律不覆蓋（ON CONFLICT DO NOTHING）。
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from chips_daily import TPEX_MIRROR_BASE
from database import get_connection, initialize_database
from official_daily_bars import TSE_DAY_COMPLETE_MIN, UTC, _save_day, fetch_json, fetch_twse_day
from price_adjust import detect_halt_jumps, load_events, market_days, parse_official_table, quote_ref_events, save_events

logger = logging.getLogger("hanstock.bars_history")
TW_TZ = timezone(timedelta(hours=8))
HISTORY_YEARS = max(1, int(os.getenv("HANSTOCK_DAILY_BARS_HISTORY_YEARS", "3")))
TWSE_DELAY_SECONDS = float(os.getenv("HANSTOCK_BARS_HISTORY_TWSE_DELAY", "2.0"))   # 證交所大約每 5 秒 3 次以內才不會被擋
TWSE_PAUSE_AFTER_FAILURES = 3
TWSE_MAX_FAILURES = 12
POLL_SECONDS = 6 * 60 * 60
BULK_INSERTED = 5000      # 這一輪補進這麼多根日K＝補了一大段歷史：黑龍表整張重算（以前歷史不夠的日子分數是空的）
OTC_DAY_COMPLETE_RATIO = 0.9
TWSE_REDUCTION_URL = "https://www.twse.com.tw/rwd/zh/reducation/TWTAUU"
TWSE_PAR_URL = "https://www.twse.com.tw/rwd/zh/change/TWTB8U"

_state: dict[str, Any] = {
    "running": False, "phase": None, "startedAt": None, "finishedAt": None, "error": None,
    "tse": {"todo": 0, "done": 0, "inserted": 0, "closed": 0, "failures": 0, "lastDate": None},
    "otc": {"months": 0, "monthsDone": 0, "inserted": 0, "days": 0, "error": None},
    "events": {"twse": 0, "tpex": 0, "tpexQuote": 0, "inferred": 0, "errors": []},
    "coverage": None, "heilong": None,
}
_lock = threading.Lock()
_run_lock = threading.Lock()
_started = False


def _now() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def _schema(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS bars_history_closed (trade_date TEXT PRIMARY KEY, noted_at TEXT NOT NULL)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS bars_history_months (
            ym TEXT PRIMARY KEY, days INTEGER NOT NULL, last_day TEXT, processed_at TEXT NOT NULL
        )"""
    )


def _processed_months() -> dict[str, tuple[int, str | None]]:
    with get_connection() as connection:
        _schema(connection)
        return {str(r["ym"]): (int(r["days"]), r["last_day"]) for r in connection.execute("SELECT * FROM bars_history_months").fetchall()}


def _mark_month(ym: str, days: int, last_day: str | None) -> None:
    with get_connection() as connection:
        _schema(connection)
        connection.execute(
            """INSERT INTO bars_history_months (ym, days, last_day, processed_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(ym) DO UPDATE SET days = excluded.days, last_day = excluded.last_day, processed_at = excluded.processed_at""",
            (ym, days, last_day, _now()),
        )


def status() -> dict[str, Any]:
    with _lock:
        return json.loads(json.dumps(_state))


def _update(**kwargs: Any) -> None:
    with _lock:
        for key, value in kwargs.items():
            if isinstance(value, dict) and isinstance(_state.get(key), dict):
                _state[key].update(value)
            else:
                _state[key] = value


def target_start(today: date | None = None, years: int = HISTORY_YEARS) -> date:
    today = today or datetime.now(TW_TZ).date()
    try:
        return today.replace(year=today.year - years)
    except ValueError:   # 2/29
        return today.replace(year=today.year - years, day=28)


def _weekdays(start: date, end: date) -> list[date]:
    out = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _counts_by_market(since: str, until: str) -> dict[str, dict[str, int]]:
    """{日期: {'TSE': n, 'OTC': n}}。"""
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT substr(b.bar_time, 1, 10) AS d, s.market AS m, COUNT(*) AS n
               FROM bars_1d b JOIN stocks s ON s.stock_code = b.stock_code
               WHERE substr(b.bar_time, 1, 10) >= ? AND substr(b.bar_time, 1, 10) <= ?
               GROUP BY d, m""",
            (since, until),
        ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(str(r["d"]), {})[str(r["m"] or "")] = int(r["n"])
    return out


def _closed_dates() -> set[str]:
    with get_connection() as connection:
        _schema(connection)
        return {str(r["trade_date"]) for r in connection.execute("SELECT trade_date FROM bars_history_closed").fetchall()}


def _mark_closed(day: str) -> None:
    with get_connection() as connection:
        _schema(connection)
        connection.execute("INSERT OR IGNORE INTO bars_history_closed (trade_date, noted_at) VALUES (?, ?)", (day, _now()))


def coverage() -> dict[str, Any]:
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT MIN(substr(bar_time, 1, 10)) AS first, MAX(substr(bar_time, 1, 10)) AS last, COUNT(*) AS n FROM bars_1d"
        ).fetchone()
        days = connection.execute(
            "SELECT COUNT(*) FROM (SELECT substr(bar_time, 1, 10) AS d FROM bars_1d GROUP BY d HAVING COUNT(*) >= ?)",
            (TSE_DAY_COMPLETE_MIN,),
        ).fetchone()[0]
    return {"firstDate": row["first"], "lastDate": row["last"], "bars": int(row["n"] or 0), "marketDays": int(days or 0),
            "targetStart": target_start().isoformat(), "years": HISTORY_YEARS}


# ------------------------------------------------------------------ 上市（證交所）

def backfill_tse(start: date, end: date, *, fetcher: Callable[[date], list[dict[str, Any]]] = fetch_twse_day,
                 delay: float = TWSE_DELAY_SECONDS, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """start～end 之間上市還沒收齊、也不是已知休市的平日，一天一份抓證交所。連續失敗就停一下，失敗太多次就收工（下一輪再補）。"""
    counts = _counts_by_market(start.isoformat(), end.isoformat())
    closed = _closed_dates()
    today = datetime.now(TW_TZ).date()
    todo = [d for d in _weekdays(start, end)
            if d.isoformat() not in closed and counts.get(d.isoformat(), {}).get("TSE", 0) < TSE_DAY_COMPLETE_MIN]
    result = {"todo": len(todo), "done": 0, "inserted": 0, "closed": 0, "failures": 0, "lastDate": None}
    _update(phase="tse", tse=dict(result))
    streak = 0
    for day in todo:
        try:
            rows = fetcher(day)
            streak = 0
        except Exception as exc:  # noqa: BLE001
            result["failures"] += 1
            streak += 1
            logger.warning("證交所 %s 抓不到：%s", day, f"{type(exc).__name__}: {exc}"[:200])
            if result["failures"] >= TWSE_MAX_FAILURES:
                result["stopped"] = "失敗太多次，下一輪再補"
                break
            sleep(60.0 if streak >= TWSE_PAUSE_AFTER_FAILURES else delay * 2)
            continue
        if rows:
            result["inserted"] += _save_day(rows)
        elif day < today:
            _mark_closed(day.isoformat())
            result["closed"] += 1
        result["done"] += 1
        result["lastDate"] = day.isoformat()
        _update(tse=dict(result))
        sleep(delay)
    _update(tse=dict(result))
    return result


# ------------------------------------------------------------------ 上櫃（鏡像）

def _download(url: str, timeout: int = 90) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        body = response.read()
        if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
            body = gzip.decompress(body)
    return body


def _mirror_get(name: str, *, volatile: bool, raw: Callable[[str], bytes] = _download) -> bytes:
    url = f"{TPEX_MIRROR_BASE}/{name}"
    if volatile:
        url += f"?v={int(time.time())}"
    return raw(url)


def load_mirror_month(ym: str, *, volatile: bool = False, raw: Callable[[str], bytes] = _download) -> dict[str, Any]:
    body = _mirror_get(f"quotes/{ym}.json.gz", volatile=volatile, raw=raw)
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return json.loads(body.decode("utf-8"))


def _last_closes_before(day: str, codes: list[str] | None = None) -> dict[str, tuple[str, float]]:
    """每檔在 day 之前最後一根日K（日期, 收盤）。"""
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT b.stock_code AS code, substr(b.bar_time, 1, 10) AS d, b.close AS c FROM bars_1d b
               JOIN (SELECT stock_code, MAX(bar_time) AS t FROM bars_1d WHERE substr(bar_time, 1, 10) < ? GROUP BY stock_code) last
               ON last.stock_code = b.stock_code AND last.t = b.bar_time""",
            (day,),
        ).fetchall()
    wanted = set(codes) if codes is not None else None
    return {str(r["code"]): (str(r["d"]), float(r["c"])) for r in rows if wanted is None or str(r["code"]) in wanted}


def import_otc_from_mirror(start: date, end: date, *, raw: Callable[[str], bytes] = _download) -> dict[str, Any]:
    """鏡像裡 start～end 的月份：上市那天有開盤（上市日K收齊）的日子，上櫃還沒收齊就補；順便用漲跌反推恢復買賣參考價找還原事件。
    每個月都會讀（事件偵測要接著前一天的收盤），只有缺的日子才寫入。"""
    result: dict[str, Any] = {"months": 0, "monthsDone": 0, "inserted": 0, "days": 0, "events": 0, "error": None}
    try:
        index = json.loads(_mirror_get("quotes/index.json", volatile=True, raw=raw).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"鏡像索引抓不到：{type(exc).__name__}: {exc}"[:200]
        _update(otc=dict(result))
        return result
    first_ym, last_ym = f"{start.year}-{start.month:02d}", f"{end.year}-{end.month:02d}"
    listed = index.get("months") or {}
    done = _processed_months()
    # 上一輪已經處理過、鏡像那個月也沒再變的就跳過（每天排程只有這個月會變）
    months = sorted(ym for ym, meta in listed.items()
                    if first_ym <= ym <= last_ym and done.get(ym) != (int((meta or {}).get("days") or 0), (meta or {}).get("last")))
    result["months"] = len(months)
    _update(phase="otc", otc=dict(result))
    if not months:
        return result
    days_all = market_days(TSE_DAY_COMPLETE_MIN)
    trading = set(days_all)
    position = {d: i for i, d in enumerate(days_all)}
    counts = _counts_by_market(start.isoformat(), end.isoformat())
    otc_counts = sorted(c.get("OTC", 0) for c in counts.values() if c.get("OTC", 0) > 0)
    typical = otc_counts[len(otc_counts) // 2] if otc_counts else 0
    current = datetime.now(TW_TZ).date()
    recent = {f"{current.year}-{current.month:02d}", f"{(current.replace(day=1) - timedelta(days=1)).year}-{(current.replace(day=1) - timedelta(days=1)).month:02d}"}
    prev_close: dict[str, tuple[str, float]] | None = None
    events: list[dict[str, Any]] = []
    for ym in months:
        try:
            month = load_mirror_month(ym, volatile=ym in recent, raw=raw)
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"{ym} 抓不到：{type(exc).__name__}: {exc}"[:200]
            continue
        for day in sorted(month.get("days") or {}):
            if day < start.isoformat() or day > end.isoformat() or day not in trading:
                continue
            rows = month["days"][day]
            if prev_close is None:
                prev_close = _last_closes_before(day)
            i = position[day]
            events.extend(quote_ref_events(day, rows, prev_close, days_all[max(0, i - 5):i][::-1]))
            have = counts.get(day, {}).get("OTC", 0)
            if have < max(100, typical * OTC_DAY_COMPLETE_RATIO):
                bars = []
                for row in rows:
                    try:
                        code, name, o, h, l, c, _chg, volume = row[:8]
                    except ValueError:
                        continue
                    bars.append({"stock_code": str(code).strip().upper(), "stock_name": str(name or code), "market": "OTC",
                                 "time": datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=UTC),
                                 "open": float(o), "high": float(h), "low": float(l), "close": float(c),
                                 "volume": max(0, int((volume or 0) / 1000))})
                result["inserted"] += _save_day(bars)
                result["days"] += 1
            for row in rows:
                try:
                    prev_close[str(row[0]).strip().upper()] = (day, float(row[5]))
                except (IndexError, TypeError, ValueError):
                    continue
        result["monthsDone"] += 1
        meta = listed.get(ym) or {}
        _mark_month(ym, int(meta.get("days") or 0), meta.get("last"))
        _update(otc=dict(result))
    if events:
        result["events"] = save_events(events)
    _update(otc=dict(result))
    return result


# ------------------------------------------------------------------ 還原事件

def refresh_official_events(start_year: int, end_year: int, *, fetcher: Callable[..., Any] = fetch_json,
                            raw: Callable[[str], bytes] = _download, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """證交所減資＋變更面額（直接抓），櫃買減資（鏡像）。"""
    out: dict[str, Any] = {"twse": 0, "tpex": 0, "errors": []}
    events: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        params = {"startDate": f"{year}0101", "endDate": f"{year}1231", "response": "json"}
        for url, kind in ((TWSE_REDUCTION_URL, "reduction"), (TWSE_PAR_URL, "par")):
            try:
                rows = parse_official_table(fetcher(url, params), source="twse", kind=kind)
                events.extend(rows)
                out["twse"] += len(rows)
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"TWSE {kind} {year}: {type(exc).__name__}: {exc}"[:200])
            sleep(TWSE_DELAY_SECONDS)
        try:
            payload = json.loads(_mirror_get(f"actions/revivt-{year}.json", volatile=year == end_year, raw=raw).decode("utf-8"))
            rows = parse_official_table(payload, source="tpex", kind="reduction")
            events.extend(rows)
            out["tpex"] += len(rows)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"TPEx revivt {year}: {type(exc).__name__}: {exc}"[:200])
    save_events(events)
    return out


def infer_events(start: date) -> int:
    """日K推測：停止買賣後跳空超過一成、又沒有其他來源事件的（一次 300 檔，記憶體不要一次全載）。"""
    days = market_days(TSE_DAY_COMPLETE_MIN)
    known = {code: [d for d, _f in events] for code, events in load_events().items()}
    with get_connection() as connection:
        codes = [str(r["stock_code"]) for r in connection.execute("SELECT DISTINCT stock_code FROM bars_1d").fetchall()]
    found: list[dict[str, Any]] = []
    for i in range(0, len(codes), 300):
        batch = codes[i:i + 300]
        placeholders = ",".join("?" for _ in batch)
        with get_connection() as connection:
            rows = connection.execute(
                f"""SELECT stock_code, substr(bar_time, 1, 10) AS d, open, close FROM bars_1d
                    WHERE stock_code IN ({placeholders}) AND substr(bar_time, 1, 10) >= ? ORDER BY stock_code, bar_time""",
                (*batch, start.isoformat()),
            ).fetchall()
        by_code: dict[str, list[tuple]] = {}
        for r in rows:
            by_code.setdefault(str(r["stock_code"]), []).append((str(r["d"]), float(r["open"] or 0), 0.0, 0.0, float(r["close"]), 0))
        found.extend(detect_halt_jumps(by_code, days, known))
    return save_events(found)


# ------------------------------------------------------------------ 一輪

def run_once(*, rebuild_heilong: bool = True) -> dict[str, Any]:
    """補一輪：上市→上櫃→還原事件→（有新東西就）重算黑龍表。同時只跑一個。"""
    if not _run_lock.acquire(blocking=False):
        return {"status": "running", **status()}
    try:
        initialize_database()
        today = datetime.now(TW_TZ).date()
        start = target_start(today)
        _update(running=True, startedAt=_now(), finishedAt=None, error=None, phase="tse")
        before = coverage()
        tse = backfill_tse(start, today)
        otc = import_otc_from_mirror(start, today)
        _update(phase="events")
        official = refresh_official_events(start.year, today.year)
        inferred = infer_events(start)
        _update(events={"twse": official["twse"], "tpex": official["tpex"], "tpexQuote": otc.get("events", 0),
                        "inferred": inferred, "errors": official["errors"][:10]})
        after = coverage()
        _update(coverage=after)
        heilong: dict[str, Any] | None = None
        changed = tse.get("inserted", 0) or otc.get("inserted", 0) or otc.get("events", 0) or inferred or (before.get("bars") != after.get("bars"))
        if rebuild_heilong and changed:
            _update(phase="heilong")
            try:
                from heilong_backtest import rebuild as rebuild_heilong_table

                bulk = int(tse.get("inserted", 0) or 0) + int(otc.get("inserted", 0) or 0) >= BULK_INSERTED
                heilong = rebuild_heilong_table(force=bulk)
            except Exception as exc:  # noqa: BLE001
                logger.exception("bars history: heilong rebuild failed")
                heilong = {"error": str(exc)}
            _update(heilong={k: heilong.get(k) for k in ("date", "dates", "rows", "written", "error") if isinstance(heilong, dict)})
        _update(running=False, phase="done", finishedAt=_now())
        return {"status": "ok", "before": before, "after": after, "tse": tse, "otc": otc, "official": official,
                "inferred": inferred, "heilong": heilong}
    except Exception as exc:  # noqa: BLE001
        logger.exception("bars history run failed")
        _update(running=False, phase="error", finishedAt=_now(), error=f"{type(exc).__name__}: {exc}"[:300])
        return {"status": "error", "error": str(exc)}
    finally:
        _run_lock.release()


def run_in_background() -> dict[str, Any]:
    """端點用：沒在跑就丟背景執行緒跑一輪，馬上回狀態。"""
    if _state.get("running"):
        return {"status": "running", **status()}
    threading.Thread(target=run_once, name="hanstock-bars-history-run", daemon=True).start()
    return {"status": "started", **status()}


def _loop() -> None:
    time.sleep(180)   # 讓日K收集器、法人先跑
    while True:
        try:
            run_once()
        except Exception:  # noqa: BLE001
            logger.exception("bars history loop failed")
        time.sleep(POLL_SECONDS)


def start_bars_history_collector() -> bool:
    global _started
    if os.getenv("HANSTOCK_BARS_HISTORY_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return False
    with _lock:
        if _started:
            return False
        _started = True
    threading.Thread(target=_loop, name="hanstock-bars-history", daemon=True).start()
    logger.info("日K三年回補已啟動：%s 年", HISTORY_YEARS)
    return True
