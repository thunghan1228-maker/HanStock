"""醞釀／發動每日保存（2026-09-25 使用者：今天醞釀 36 檔、發動 15 檔，明天會不會不見？要永久保存）。

醞釀是「上一個交易日收盤」算出來的名單，每個交易日都不一樣；發動是盤中即時判斷的，
收盤後價格一停就沒有「現在正在發動」這回事。所以這裡把兩種都存進 SQLite：
  - 醞釀快照：每個交易日第一次算出醞釀名單就存（一天一份，重算不覆蓋）
  - 發動紀錄：盤中每 30 秒用證交所 MIS 即時報價（跟首頁同一個來源）掃 43 個族群，
    一檔股票一天第一次符合發動就記一筆（時間、價格、漲幅、均線分數、預估周轉、量比）
發動條件跟前端 brewLiveMetrics 一模一樣；金融股（後端標 skipped）不算。
前端醞釀／發動分頁的「昨天／前天」看的就是這裡存的資料；今天的發動也會把「盤中曾經發動、
現在回落」的一起列出來。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

logger = logging.getLogger("hanstock.brew_launch_history")
TW_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = max(15, int(os.getenv("HANSTOCK_BREW_LAUNCH_SCAN_SECONDS", "30")))
MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_CHUNK = 80
MARKET_OPEN_MINUTE = 9 * 60
MARKET_SCAN_END_MINUTE = 13 * 60 + 35  # 13:30 收盤，最後一盤成交後再掃幾分鐘
BREW_DETAIL_KEYS = ("prevClose", "boxHigh", "boxLow", "boxRangePct", "maSpreadPct", "score")
LAUNCH_DETAIL_KEYS = ("changePct", "boxHigh", "projTurnoverPct", "volRatio", "brewing")

_started = False
_lock = threading.Lock()
_state: dict[str, Any] = {"lastPollAt": None, "lastPollResult": None, "lastError": None}


def _enabled() -> bool:
    return os.getenv("HANSTOCK_BREW_LAUNCH_SCAN_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


# ------------------------------------------------------------------ 資料表

def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS brew_launch_daily (
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            kind TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            price REAL,
            score INTEGER,
            detail_json TEXT,
            PRIMARY KEY (trade_date, stock_code, kind)
        )"""
    )


def _rows(connection, trade_date: str, kind: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT stock_code, recorded_at, price, score, detail_json FROM brew_launch_daily WHERE trade_date = ? AND kind = ? ORDER BY recorded_at, stock_code",
        (trade_date, kind),
    ).fetchall()
    out = []
    for row in rows:
        group, name = group_and_name(str(row["stock_code"]))
        out.append({
            "code": str(row["stock_code"]), "name": name, "group": group, "recordedAt": row["recorded_at"],
            "price": row["price"], "score": row["score"], **(json.loads(row["detail_json"]) if row["detail_json"] else {}),
        })
    return out


def record_brew_snapshot(payload: dict[str, Any]) -> int:
    """把這個交易日的醞釀名單存起來（已經有這天的就不動）。回傳新增筆數。"""
    session = str(payload.get("session") or "")
    stocks = payload.get("stocks") or {}
    if not session or not stocks:
        return 0
    initialize_database()
    recorded_at = datetime.now(TW_TZ).isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        exists = connection.execute(
            "SELECT 1 FROM brew_launch_daily WHERE trade_date = ? AND kind = 'brew' LIMIT 1", (session,)
        ).fetchone()
        if exists:
            return 0
        before = connection.total_changes
        connection.executemany(
            "INSERT OR IGNORE INTO brew_launch_daily (trade_date, stock_code, kind, recorded_at, price, score, detail_json) VALUES (?, ?, 'brew', ?, ?, ?, ?)",
            [
                (session, code, recorded_at, info.get("prevClose"), info.get("score"),
                 json.dumps({k: info.get(k) for k in BREW_DETAIL_KEYS}, ensure_ascii=False))
                for code, info in stocks.items() if info.get("brewing") and not info.get("skipped")
            ],
        )
        return connection.total_changes - before


def record_launches(trade_date: str, rows: list[dict[str, Any]], recorded_at: str) -> int:
    with get_connection() as connection:
        _schema(connection)
        before = connection.total_changes
        connection.executemany(
            "INSERT OR IGNORE INTO brew_launch_daily (trade_date, stock_code, kind, recorded_at, price, score, detail_json) VALUES (?, ?, 'launch', ?, ?, ?, ?)",
            [
                (trade_date, row["code"], recorded_at, row["price"], row["score"],
                 json.dumps({k: row.get(k) for k in LAUNCH_DETAIL_KEYS}, ensure_ascii=False))
                for row in rows
            ],
        )
        return connection.total_changes - before


def launched_codes(trade_date: str) -> set[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute(
            "SELECT stock_code FROM brew_launch_daily WHERE trade_date = ? AND kind = 'launch'", (trade_date,)
        ).fetchall()
    return {str(row["stock_code"]) for row in rows}


def history(*, days: int = 10, date: str | None = None) -> dict[str, Any]:
    """{dates: [最近的在前], days: {date: {brew: [...], launch: [...]}}}；指定 date 就只回那天。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        if date:
            dates = [date]
        else:
            rows = connection.execute(
                "SELECT DISTINCT trade_date FROM brew_launch_daily ORDER BY trade_date DESC LIMIT ?", (max(1, min(int(days), 60)),)
            ).fetchall()
            dates = [str(row["trade_date"]) for row in rows]
        return {
            "status": "ok", "dates": dates,
            "days": {day: {"brew": _rows(connection, day, "brew"), "launch": _rows(connection, day, "launch")} for day in dates},
        }


# ------------------------------------------------------------------ 發動判斷（跟前端同一套）

_group_by_code: dict[str, tuple[str, str]] = {}


def group_and_name(code: str) -> tuple[str, str]:
    if not _group_by_code:
        for name, members in STOCK_GROUPS.items():
            if name in SPECIAL_GROUP_NAMES:
                continue
            for member_code, stock_name in members:
                _group_by_code.setdefault(str(member_code).strip().upper(), (name, str(stock_name)))
    return _group_by_code.get(code, ("", code))


def in_scan_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return MARKET_OPEN_MINUTE <= minute < MARKET_SCAN_END_MINUTE


def volume_factor(quote_date: str | None, quote_time: str | None, today: str) -> float:
    """盤中累積量 → 全天預估量的放大倍數（跟前端 brewVolumeFactor 一樣）：09:00～13:30 共 270 分鐘，
    依已經過的時間等比放大，最多 4 倍；報價不是今天盤中的就不放大。"""
    if not quote_date or quote_date != today or not quote_time:
        return 1.0
    try:
        hour, minute = int(quote_time[:2]), int(quote_time[3:5])
    except ValueError:
        return 1.0
    elapsed = hour * 60 + minute - MARKET_OPEN_MINUTE
    if elapsed <= 0 or elapsed >= 270:
        return 1.0
    return min(270 / elapsed, 4.0)


def live_score(ma_sums: dict[str, Any], price: float, periods: list[int]) -> int:
    mas = {p: (float(ma_sums[str(p)]) + price) / p for p in periods}
    ordered = sorted(mas)
    return sum(1 for i in range(len(ordered)) for j in range(i + 1, len(ordered)) if mas[ordered[i]] > mas[ordered[j]])


def evaluate_launch(info: dict[str, Any], quote: dict[str, Any], factor: float, rules: dict[str, Any]) -> dict[str, Any] | None:
    price = quote.get("price")
    if not price or price <= 0 or info.get("skipped"):
        return None
    box_high = float(info.get("boxHigh") or 0)
    if box_high <= 0 or price <= box_high:
        return None
    score = live_score(info["maSums"], price, list(rules["maPeriods"]))
    if score < int(rules["launchMinScore"]):
        return None
    proj_volume = float(quote.get("volume") or 0) * factor
    shares = float(info.get("sharesLots") or 0)
    avg5 = float(info.get("avgVol5") or 0)
    proj_turnover = proj_volume / shares * 100 if shares > 0 else None
    vol_ratio = proj_volume / avg5 if avg5 > 0 else None
    volume_ok = (proj_turnover is not None and proj_turnover >= float(rules["turnoverMinPct"])) or \
        (vol_ratio is not None and vol_ratio >= float(rules["volumeRatioMin"]))
    if not volume_ok:
        return None
    prev_close = quote.get("prevClose")
    return {
        "price": price, "score": score, "boxHigh": box_high,
        "changePct": round((price / prev_close - 1) * 100, 2) if prev_close else None,
        "projTurnoverPct": round(proj_turnover, 2) if proj_turnover is not None else None,
        "volRatio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "brewing": bool(info.get("brewing")),
    }


# ------------------------------------------------------------------ 證交所 MIS 即時報價（跟首頁同來源）

def _num(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number == number and number > 0 else None


def _book(value: Any) -> float | None:
    return _num(str(value or "").split("_")[0])


def _default_fetcher(url: str) -> Any:
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        "User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)",
    })
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        return json.load(response)


def fetch_mis_quotes(codes: list[str], markets: dict[str, str], *, fetcher: Callable[[str], Any] | None = None) -> dict[str, dict[str, Any]]:
    """{代號: {price, prevClose, volume(張), quoteDate, quoteTime}}；市場別不確定的上市、上櫃都問。
    沒成交的那盤 z 是 "-"：漲停鎖死只剩委買、跌停只剩委賣，用委買／委賣推算（跟首頁一樣）。"""
    call = fetcher or _default_fetcher
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(codes), MIS_CHUNK):
        channels: list[str] = []
        for code in codes[start:start + MIS_CHUNK]:
            market = (markets.get(code) or "").upper()
            channels += [f"tse_{code}.tw"] if market == "TSE" else [f"otc_{code}.tw"] if market == "OTC" else [f"tse_{code}.tw", f"otc_{code}.tw"]
        params = urllib.parse.urlencode({"ex_ch": "|".join(channels), "json": "1", "delay": "0", "_": str(int(time.time() * 1000))})
        payload = call(f"{MIS_URL}?{params}")
        for item in (payload.get("msgArray") or []) if isinstance(payload, dict) else []:
            code = str(item.get("c") or "").strip().upper()
            if not code:
                continue
            prev_close = _num(item.get("y"))
            price = _num(item.get("z"))
            if price is None:
                bid, ask = _book(item.get("b")), _book(item.get("a"))
                price = bid if bid and not ask else ask if ask and not bid else (bid + ask) / 2 if bid and ask else None
            if price is None:
                price = _num(item.get("o")) or _num(item.get("h")) or _num(item.get("l")) or prev_close
            if price is None:
                continue
            day = str(item.get("d") or "")
            out[code] = {
                "price": price, "prevClose": prev_close, "volume": int(_num(item.get("v")) or 0),
                "quoteDate": f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else None,
                "quoteTime": str(item.get("t") or "") or None,
            }
    return out


def _markets(codes: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"SELECT stock_code, market FROM stocks WHERE stock_code IN ({','.join('?' for _ in batch)})", tuple(batch)
            ).fetchall()
            for row in rows:
                if row["market"]:
                    out[str(row["stock_code"]).upper()] = str(row["market"]).upper()
    return out


# ------------------------------------------------------------------ 主流程

def scan_once(
    *, now: datetime | None = None, payload: dict[str, Any] | None = None,
    quotes_fetcher: Callable[[str], Any] | None = None, quotes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """掃一輪：存醞釀快照（一天一次），盤中判斷發動並記錄第一次發動。"""
    now = now or datetime.now(TW_TZ)
    if payload is None:
        from brew_launch import get_brew_launch

        payload = get_brew_launch()
    snapshot = record_brew_snapshot(payload)
    if not in_scan_window(now):
        return {"status": "skipped", "reason": "不在盤中（週一～五 09:00～13:35 才掃發動）", "brewSnapshot": snapshot}
    today = now.strftime("%Y-%m-%d")
    if str(payload.get("session") or today) != today:
        return {"status": "skipped", "reason": f"醞釀資料的交易日 {payload.get('session')} 不是今天", "brewSnapshot": snapshot}
    rules = payload["rules"]
    stocks = {code: info for code, info in (payload.get("stocks") or {}).items() if not info.get("skipped")}
    already = launched_codes(today)
    candidates = sorted(code for code in stocks if code not in already)
    if not candidates:
        return {"status": "ok", "checked": 0, "launched": 0, "launchedToday": len(already), "brewSnapshot": snapshot}
    if quotes is None:
        quotes = fetch_mis_quotes(candidates, _markets(candidates), fetcher=quotes_fetcher)
    latest = max(((q.get("quoteDate") or ""), (q.get("quoteTime") or "")) for q in quotes.values()) if quotes else ("", "")
    factor = volume_factor(latest[0] or None, latest[1] or None, today)
    launched: list[dict[str, Any]] = []
    for code in candidates:
        quote = quotes.get(code)
        if not quote:
            continue
        metrics = evaluate_launch(stocks[code], quote, factor, rules)
        if metrics:
            launched.append({"code": code, **metrics})
    if launched:
        record_launches(today, launched, now.isoformat(timespec="seconds"))
    return {
        "status": "ok", "checked": len(candidates), "launched": len(launched), "codes": [row["code"] for row in launched],
        "factor": round(factor, 2), "launchedToday": len(already) + len(launched), "brewSnapshot": snapshot,
    }


def scan_status() -> dict[str, Any]:
    return {"enabled": _enabled(), "pollSeconds": POLL_SECONDS, "scanWindow": "週一～五 09:00～13:35",
            "inWindowNow": in_scan_window(datetime.now(TW_TZ)), **_state}


def _loop() -> None:
    while True:
        try:
            result = scan_once()
            _state.update({"lastPollAt": datetime.now(TW_TZ).isoformat(timespec="seconds"), "lastPollResult": result, "lastError": None})
            if result.get("launched"):
                logger.info("發動紀錄: %s", result)
        except Exception as error:  # noqa: BLE001
            _state.update({"lastPollAt": datetime.now(TW_TZ).isoformat(timespec="seconds"), "lastError": f"{type(error).__name__}: {error}"[:300]})
            logger.exception("醞釀／發動掃描失敗")
        time.sleep(POLL_SECONDS if in_scan_window(datetime.now(TW_TZ)) else 120)


def start_brew_launch_scan() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if not _enabled():
            logger.info("醞釀／發動掃描已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-brew-launch-scan", daemon=True).start()
        _started = True
        return True
