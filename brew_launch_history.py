"""醞釀／發動每日保存（2026-09-25 使用者：今天醞釀 36 檔、發動 15 檔，明天會不會不見？要永久保存）。

醞釀是「上一個交易日收盤」算出來的名單，每個交易日都不一樣；發動是盤中即時判斷的，
收盤後價格一停就沒有「現在正在發動」這回事。所以這裡把兩種都存進 SQLite：
  - 醞釀快照：每個交易日第一次算出醞釀名單就存（一天一份，重算不覆蓋）
  - 發動紀錄：盤中每 30 秒用證交所 MIS 即時報價（跟首頁同一個來源）掃 43 個族群全部股票，
    一檔股票一天第一次符合發動就記一筆（時間、價格、漲幅、均線分數、周轉率、量比）；
    同一檔今天可以分好幾次發動（發動→回落→再發動，2026-09-29 使用者：時間要跟著最新那次更新）——
    第一次的 recorded_at 永久保留不會被蓋掉（回查用），每次「從沒發動變發動」另外更新
    latest_recorded_at 跟當次的價格／分數／細節（讓還在發動中的股票看得到「這一次」是幾點開始的）。
發動條件跟前端 brewLiveMetrics 一模一樣；金融股（後端標 skipped）不算。
前端醞釀／發動分頁的「昨天／前天」看的就是這裡存的資料；今天的發動也會把「盤中曾經發動、
現在回落」的一起列出來。
保存功能上線前的日子、或程式那天沒在跑：開機時（以及每天 15:30 後）用日K回推最近幾個交易日——
醞釀快照照那天盤前的算法補；發動用收盤價判斷（收盤過箱頂、收盤均線分數夠、全天量夠），標 eod＝收盤回推，
盤中曾發動又回落的補不回來。同一檔同一天只留一筆，已有盤中紀錄的不會被蓋掉。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS
from trading_days import is_trading_day

logger = logging.getLogger("hanstock.brew_launch_history")
TW_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = max(15, int(os.getenv("HANSTOCK_BREW_LAUNCH_SCAN_SECONDS", "30")))
MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_CHUNK = 80
MARKET_OPEN_MINUTE = 9 * 60
MARKET_SCAN_END_MINUTE = 13 * 60 + 35  # 13:30 收盤，最後一盤成交後再掃幾分鐘
CLOSING_AUCTION_START = "13:25:00"  # 13:25～13:30 收盤集合競價：證交所只揭示試撮價，不是真的成交
CLOSING_AUCTION_END = "13:30:00"
CLOSING_AUCTION_CHECK_FROM = "13:26:00"  # 清假紀錄：這之後記的才核對收盤價（13:25 那一分鐘可能是 13:25 前最後成交才掃到的）
CLOSING_CHECK_AFTER = "13:31:00"  # 收盤價出來以後才核對
BREW_DETAIL_KEYS = ("prevClose", "boxHigh", "boxLow", "boxRangePct", "maSpreadPct", "score")
LAUNCH_DETAIL_KEYS = ("changePct", "boxHigh", "projTurnoverPct", "volRatio", "brewing", "eod", "limitUp", "limitDown",
                      "strengthPct", "netAmount", "holderLabel")
BACKFILL_DAYS = max(0, int(os.getenv("HANSTOCK_BREW_LAUNCH_BACKFILL_DAYS", "3")))  # 用日K回推最近幾個交易日
BACKFILL_MINUTE = 15 * 60 + 30  # 每天 15:30 後（當天日K進來了）再回推一次，把當天掃描漏掉的補齊
DAY_COMPLETE_RATIO = 0.75  # 那天的日K要有這麼多比例的族群股才算完整（上櫃還沒補進來就先不回推）

_started = False
_lock = threading.Lock()
_state: dict[str, Any] = {"lastPollAt": None, "lastPollResult": None, "lastError": None, "backfill": None, "backfillDate": None}


def _enabled() -> bool:
    return os.getenv("HANSTOCK_BREW_LAUNCH_SCAN_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


# ------------------------------------------------------------------ 資料表

def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS brew_launch_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            kind TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            price REAL,
            score INTEGER,
            detail_json TEXT
        )"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_brew_launch_daily_date_kind ON brew_launch_daily(trade_date, kind)")
    columns = {str(r["name"]) for r in connection.execute("PRAGMA table_info(brew_launch_daily)").fetchall()}
    if "id" not in columns:
        # 舊表結構是 (trade_date, stock_code, kind) 當主鍵，同一檔同一天只能有一筆；
        # 2026-09-29 使用者：同一檔股票今天可以分好幾次發動，每一次都要各自留一筆、不能互相蓋掉——
        # 改成允許同一檔同一天多筆，舊資料原封不動搬過去（不管舊表有沒有 latest_recorded_at 欄位都只取共同欄位）。
        connection.execute("ALTER TABLE brew_launch_daily RENAME TO brew_launch_daily_old")
        connection.execute(
            """CREATE TABLE brew_launch_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, kind TEXT NOT NULL,
                recorded_at TEXT NOT NULL, price REAL, score INTEGER, detail_json TEXT
            )"""
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_brew_launch_daily_date_kind ON brew_launch_daily(trade_date, kind)")
        connection.execute(
            "INSERT INTO brew_launch_daily (trade_date, stock_code, kind, recorded_at, price, score, detail_json) "
            "SELECT trade_date, stock_code, kind, recorded_at, price, score, detail_json FROM brew_launch_daily_old"
        )
        connection.execute("DROP TABLE brew_launch_daily_old")


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
    """收盤價回推（backfill）專用：只幫「今天完全沒有任何發動紀錄」的股票補一筆；已經有盤中紀錄
    （不管幾筆）的股票不動，不能讓收盤價回推蓋掉或摻進盤中已經記到的紀錄。"""
    with get_connection() as connection:
        _schema(connection)
        existing = {str(r["stock_code"]) for r in connection.execute(
            "SELECT DISTINCT stock_code FROM brew_launch_daily WHERE trade_date = ? AND kind = 'launch'", (trade_date,)
        ).fetchall()}
        fresh = [row for row in rows if row["code"] not in existing]
        connection.executemany(
            "INSERT INTO brew_launch_daily (trade_date, stock_code, kind, recorded_at, price, score, detail_json) VALUES (?, ?, 'launch', ?, ?, ?, ?)",
            [
                (trade_date, row["code"], recorded_at, row["price"], row["score"],
                 json.dumps({k: row.get(k) for k in LAUNCH_DETAIL_KEYS}, ensure_ascii=False))
                for row in fresh
            ],
        )
        return len(fresh)


def record_launch_episode(trade_date: str, rows: list[dict[str, Any]], recorded_at: str) -> int:
    """盤中即時掃描專用：這些股票這一輪「從沒發動變發動」，各自新增一筆（不是覆蓋）。
    2026-09-29 使用者：同一檔股票今天可以分好幾次發動（發動→回落→再發動），每一次都要留下自己的紀錄，
    不能把稍早的（尤其是第一次）蓋掉——所以這裡永遠是新增，不會動到任何一筆舊紀錄。"""
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            "INSERT INTO brew_launch_daily (trade_date, stock_code, kind, recorded_at, price, score, detail_json) VALUES (?, ?, 'launch', ?, ?, ?, ?)",
            [
                (trade_date, row["code"], recorded_at, row["price"], row["score"],
                 json.dumps({k: row.get(k) for k in LAUNCH_DETAIL_KEYS}, ensure_ascii=False))
                for row in rows
            ],
        )
        return len(rows)


def purge_false_relaunches(
    *, trade_date: str | None = None, dry_run: bool = True, payload: dict[str, Any] | None = None,
    bars_loader: Callable[[str], list[dict[str, Any]]] | None = None,
    closing_loader: Callable[[list[str]], dict[str, dict[str, Any]]] | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """2026-10-05 使用者：把當天確定是假的「重新發動」紀錄刪掉（修正前報價怪掉灌進去的，鼎元漲停鎖死
    一早上記了 14 筆）。同一檔第二筆以後的每一筆，看它跟前一筆（留下來的）之間的 1 分K：有成交的價格從沒跌到箱頂
    （含）以下、用那段最低價算的均線分數也還在門檻以上（分數只會隨價格越低越少，量是累積的不會變少），
    就代表中間根本沒回落，這筆是假的，刪掉。中間有任何一根 1 分K 跌到箱頂以下、分數掉下門檻、或抓不到
    1 分K 的，都保留；第一筆不看這一條。只能清醞釀資料那個交易日（要用當天的均線合計算分數）。
    收盤後另外核對收盤試撮時段（13:26 以後）記到的紀錄（包括當天第一筆）：那段沒有真的成交，只有 13:30
    收盤那一盤是真的，收盤那一盤沒成交、或收盤價不符合發動條件的，就是試撮價造成的假紀錄，刪掉；
    證交所報價抓不到這檔的不能確定，保留。
    dry_run=True 只回報會刪哪些、不動資料。"""
    if payload is None:
        from brew_launch import get_brew_launch

        payload = get_brew_launch()
    session = str(payload.get("session") or "")
    trade_date = trade_date or session
    if not session or trade_date != session:
        return {"status": "error", "reason": f"只能清醞釀資料的交易日 {session or '（沒有）'}", "tradeDate": trade_date}
    rules = payload["rules"]
    stocks = payload.get("stocks") or {}
    periods = list(rules["maPeriods"])
    min_score = int(rules["launchMinScore"])
    if bars_loader is None:
        from stock_history_service import get_stock_history_bars_1m

        def bars_loader(code: str) -> list[dict[str, Any]]:
            bars = get_stock_history_bars_1m(code, calendar_days=3).get("bars") or []
            return [b for b in bars if datetime.fromtimestamp(int(b["ts"]) / 1000, TW_TZ).strftime("%Y-%m-%d") == trade_date]

    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        records = connection.execute(
            "SELECT id, stock_code, recorded_at FROM brew_launch_daily WHERE trade_date = ? AND kind = 'launch' ORDER BY stock_code, recorded_at, id",
            (trade_date,),
        ).fetchall()
    by_code: dict[str, list[Any]] = {}
    for row in records:
        by_code.setdefault(str(row["stock_code"]), []).append(row)
    errors: list[dict[str, str]] = []
    now = now or datetime.now(TW_TZ)
    closed = (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")) >= (trade_date, CLOSING_CHECK_AFTER)
    auction_codes = sorted(code for code, recs in by_code.items()
                           if any(str(r["recorded_at"])[11:19] >= CLOSING_AUCTION_CHECK_FROM for r in recs))
    closing: dict[str, dict[str, Any]] = {}
    if auction_codes and closed:
        try:
            closing = (closing_loader or (lambda codes: _closing_quotes(codes, trade_date)))(auction_codes)
        except Exception as error:  # noqa: BLE001
            errors.append({"code": "收盤報價", "error": f"{type(error).__name__}: {error}"[:200]})
    to_delete: list[int] = []
    by_stock: dict[str, dict[str, Any]] = {}
    for code, recs in by_code.items():
        info = stocks.get(code)
        if not info or info.get("skipped") or not info.get("maSums") or (len(recs) < 2 and code not in closing):
            continue
        bars: list[dict[str, Any]] | None = None
        if len(recs) >= 2:
            try:
                bars = sorted(bars_loader(code), key=lambda b: int(b["ts"]))
            except Exception as error:  # noqa: BLE001
                errors.append({"code": code, "error": f"{type(error).__name__}: {error}"[:200]})
        box_high = float(info.get("boxHigh") or 0)
        removed: list[str] = []
        auction_removed: list[str] = []
        kept_prev = None
        for cur in recs:
            hms = str(cur["recorded_at"])[11:19]
            fake = False
            if kept_prev is not None and bars is not None and box_high > 0:
                start = datetime.fromisoformat(str(kept_prev["recorded_at"])).replace(second=0, microsecond=0)
                end = datetime.fromisoformat(str(cur["recorded_at"]))
                window = [b for b in bars if start.timestamp() * 1000 <= int(b["ts"]) < end.timestamp() * 1000]
                if window:                                  # 查不到 1 分K：不能確定，保留
                    # 2026-10-05：1 分K 改從 Yahoo 補的時候價格是 float32（69.3 變 69.30000305175781），
                    # 剛好跌到箱頂 69.3 會被當成還在箱頂上；證交所價格最多 4 位小數，先四捨五入
                    low = round(min(float(b["low"]) for b in window), 4)
                    fake = low > box_high and live_score(info["maSums"], low, periods) >= min_score   # 中間沒回落過
            if not fake and code in closing and hms >= CLOSING_AUCTION_CHECK_FROM and not _closing_trade_launch(info, closing[code], rules):
                fake = True
                auction_removed.append(hms)
            if fake:
                to_delete.append(int(cur["id"]))
                removed.append(hms)
            else:
                kept_prev = cur
        by_stock[code] = {"before": len(recs), "removed": len(removed), "after": len(recs) - len(removed),
                          "removedTimes": removed, "closingAuctionTimes": auction_removed}
    if to_delete and not dry_run:
        with get_connection() as connection:
            connection.executemany("DELETE FROM brew_launch_daily WHERE id = ? AND kind = 'launch'", [(i,) for i in to_delete])
    return {"status": "ok", "tradeDate": trade_date, "dryRun": dry_run, "removed": len(to_delete),
            "stocks": {code: v for code, v in by_stock.items() if v["removed"]}, "checked": len(by_stock), "errors": errors,
            "closingCheck": {"ran": bool(closing), "waitingForClose": bool(auction_codes) and not closed,
                             "quotes": {code: {k: q.get(k) for k in ("price", "quoteTime", "priceSource", "volume")} for code, q in closing.items()}}}


def _closing_quotes(codes: list[str], trade_date: str) -> dict[str, dict[str, Any]]:
    """收盤後證交所 MIS 的報價（隔天開盤前都還是那天的），只留那個交易日的。"""
    quotes = fetch_mis_quotes(codes, _markets(codes))
    return {code: quote for code, quote in quotes.items() if quote.get("quoteDate") == trade_date}


def _closing_trade_launch(info: dict[str, Any], quote: dict[str, Any], rules: dict[str, Any]) -> bool:
    """13:30 收盤那一盤真的有成交，而且收盤價（含全天量）符合發動條件。"""
    if quote.get("priceSource") != "trade" or str(quote.get("quoteTime") or "") < CLOSING_AUCTION_END:
        return False
    return evaluate_launch(info, quote, rules) is not None


def launched_codes(trade_date: str) -> set[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute(
            "SELECT stock_code FROM brew_launch_daily WHERE trade_date = ? AND kind = 'launch'", (trade_date,)
        ).fetchall()
    return {str(row["stock_code"]) for row in rows}


def backfill_holder_force(dates: list[str] | None = None, *, limit_dates: int = 10) -> dict[str, Any]:
    """2026-10-03 使用者：「今天曾發動」列表的盤中大戶力整排都是「—」。根因：補這個欄位的
    scan_once() 改法（2026-10-03 上線）是在那天收盤後才上線的，上線前盤中就記下來的發動永久紀錄
    detail_json 裡根本沒有 strengthPct 這些欄位，不會因為後來上線就自動補上——這裡用當時存的
    trade_date 重新查一次主力排行（整個交易日累計，跟後來「今天／昨天／前天」大戶力分頁看到的
    是同一份資料）補回去。冪等：strengthPct 已經有值（不是 None）的列不會重查；查不到資料的
    還是 None，之後主力資料更完整了可以再跑一次。"""
    from main_force_store import load_main_force_ranking

    initialize_database()
    summary: dict[str, Any] = {"at": datetime.now(TW_TZ).isoformat(timespec="seconds"), "days": []}
    with get_connection() as connection:
        _schema(connection)
        target_dates = dates
        if target_dates is None:
            rows = connection.execute(
                "SELECT DISTINCT trade_date FROM brew_launch_daily WHERE kind = 'launch' ORDER BY trade_date DESC LIMIT ?",
                (max(1, int(limit_dates)),),
            ).fetchall()
            target_dates = [str(row["trade_date"]) for row in rows]
        for day in target_dates:
            day_rows = connection.execute(
                "SELECT id, stock_code, detail_json FROM brew_launch_daily WHERE trade_date = ? AND kind = 'launch'", (day,)
            ).fetchall()
            details_by_id = {row["id"]: (json.loads(row["detail_json"]) if row["detail_json"] else {}) for row in day_rows}
            candidates = [row for row in day_rows if details_by_id[row["id"]].get("strengthPct") is None]
            if not candidates:
                summary["days"].append({"date": day, "rows": len(day_rows), "candidates": 0, "updated": 0})
                continue
            codes = sorted({str(row["stock_code"]) for row in candidates})
            try:
                ranking = load_main_force_ranking(day, codes=codes)
            except Exception as error:  # noqa: BLE001
                summary["days"].append({
                    "date": day, "rows": len(day_rows), "candidates": len(candidates), "updated": 0,
                    "error": f"{type(error).__name__}: {error}"[:200],
                })
                continue
            holder_by_code = {row["code"]: row for row in ranking}
            updated = 0
            for row in candidates:
                holder = holder_by_code.get(str(row["stock_code"]))
                if not holder:
                    continue
                detail = details_by_id[row["id"]]
                detail["strengthPct"] = holder.get("strengthPct")
                detail["netAmount"] = holder.get("netAmount")
                detail["holderLabel"] = holder.get("holderLabel")
                connection.execute(
                    "UPDATE brew_launch_daily SET detail_json = ? WHERE id = ?",
                    (json.dumps(detail, ensure_ascii=False), row["id"]),
                )
                updated += 1
            summary["days"].append({"date": day, "rows": len(day_rows), "candidates": len(candidates), "updated": updated})
    return summary


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


# ------------------------------------------------------------------ 用日K回推（上線前的日子／當天沒掃到）

def _past_bar_dates(session: str, limit: int, *, include_session: bool) -> list[str]:
    """日K表裡 session 之前（含 session 當天要 include_session）最近的幾個交易日，新的在前。"""
    if limit <= 0:
        return []
    initialize_database()
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT DISTINCT substr(bar_time, 1, 10) AS d FROM bars_1d WHERE substr(bar_time, 1, 10) {'<=' if include_session else '<'} ? ORDER BY d DESC LIMIT ?",
            (session, limit),
        ).fetchall()
    return [str(row["d"]) for row in rows]


def _day_bars(codes: list[str], day: str) -> dict[str, tuple[float, int]]:
    """{代號: (收盤, 全天量張)}：那天的日K。"""
    out: dict[str, tuple[float, int]] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"SELECT stock_code, close, volume FROM bars_1d WHERE substr(bar_time, 1, 10) = ? AND stock_code IN ({','.join('?' for _ in batch)})",
                (day, *batch),
            ).fetchall()
            for row in rows:
                out[str(row["stock_code"]).strip().upper()] = (float(row["close"]), int(row["volume"] or 0))
    return out


def purge_non_trading_days() -> list[str]:
    """把存到非交易日（週末、國定假日）名下的紀錄清掉：休市日曆補上之前，程式只看星期幾，
    中秋節那天（2026-09-25）凌晨就存了一份醞釀快照，前端會多出一個「那天沒有股票發動」的假日子。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT DISTINCT trade_date FROM brew_launch_daily").fetchall()
        bad = sorted(str(row["trade_date"]) for row in rows if not is_trading_day(str(row["trade_date"])))
        for day in bad:
            connection.execute("DELETE FROM brew_launch_daily WHERE trade_date = ?", (day,))
    return bad


def backfill_past_days(*, session: str | None = None, days: int | None = None, now: datetime | None = None) -> dict[str, Any]:
    """用日K回推最近幾個交易日的紀錄（保存功能上線前的日子，或那天程式沒在跑）。
    醞釀快照：那天盤前用「那天之前」的日K算的名單，跟當天看到的一樣（已經有就不動）。
    發動：那天收盤價過箱頂、收盤均線分數 ≥ 11、全天量夠（周轉或量比），標 eod＝收盤回推；
    盤中曾發動又回落的補不回來。同一檔同一天只留一筆，已有盤中紀錄的不會被蓋掉。
    session 當天要 15:30 後（那天的日K進來了）才回推；那天的日K還不完整（上櫃沒補進來）就先跳過。"""
    from brew_launch import compute_brew_launch, group_codes, session_date

    now = now or datetime.now(TW_TZ)
    session = session or session_date(now)
    today = now.strftime("%Y-%m-%d")
    include_session = today > session or (today == session and now.hour * 60 + now.minute >= BACKFILL_MINUTE)
    codes = group_codes()
    summary: dict[str, Any] = {"session": session, "at": now.isoformat(timespec="seconds"), "purged": purge_non_trading_days(), "days": []}
    for day in _past_bar_dates(session, BACKFILL_DAYS if days is None else days, include_session=include_session):
        day_bars = _day_bars(codes, day)
        if len(day_bars) < DAY_COMPLETE_RATIO * len(codes):
            summary["days"].append({"date": day, "status": "skipped", "reason": f"那天的日K只有 {len(day_bars)}/{len(codes)} 檔，還不完整"})
            continue
        payload = compute_brew_launch(session=day)
        brew_added = record_brew_snapshot(payload)
        rules = payload["rules"]
        launched: list[dict[str, Any]] = []
        for code, info in (payload.get("stocks") or {}).items():
            bar = day_bars.get(code)
            if not bar or info.get("skipped"):
                continue
            metrics = evaluate_launch(info, {"price": bar[0], "prevClose": info.get("prevClose"), "volume": bar[1]}, rules)
            if metrics:
                launched.append({"code": code, **metrics, "eod": True})
        launch_added = record_launches(day, launched, f"{day}T13:30:00+08:00") if launched else 0
        summary["days"].append({"date": day, "status": "ok", "brewAdded": brew_added, "eodLaunches": len(launched), "launchAdded": launch_added})
    return summary


def _backfill_due(now: datetime) -> bool:
    """開機先回推一次；之後每天 15:30 後再一次（那天的日K進來後，把當天掃描漏掉的用收盤價補齊）。"""
    if _state.get("backfillDate") is None:
        return True
    return now.hour * 60 + now.minute >= BACKFILL_MINUTE and _state["backfillDate"] != now.strftime("%Y-%m-%d")


def _run_backfill(now: datetime) -> None:
    try:
        _state["backfill"] = backfill_past_days(now=now)
    except Exception as error:  # noqa: BLE001
        _state["backfill"] = {"status": "error", "at": now.isoformat(timespec="seconds"), "error": f"{type(error).__name__}: {error}"[:300]}
        logger.exception("醞釀／發動日K回推失敗")
    # 15:30 前跑的（開機）不算當天那次，15:30 後還要再跑一次
    _state["backfillDate"] = now.strftime("%Y-%m-%d") if now.hour * 60 + now.minute >= BACKFILL_MINUTE else ""


# ------------------------------------------------------------------ 發動判斷（跟前端同一套）

_group_by_code: dict[str, tuple[str, str]] = {}
# 2026-10-05 使用者：創高黑龍的今日名單裡，不在族群表的股票（聯傑、宏齊、南茂…）股名欄只顯示代號。
# 族群表找不到的改查 stocks 資料表（日K收集時存的官方中文股名），一小時重讀一次；
# 讀失敗或表還是空的（剛開機）一分鐘後再試。
DB_NAMES_TTL = 3600
_db_names: dict[str, str] = {}
_db_names_state: dict[str, Any] = {"path": None, "at": 0.0}
_db_names_lock = threading.Lock()


def _db_stock_name(code: str) -> str | None:
    import database

    path, now = str(database.DATABASE_PATH), time.time()
    with _db_names_lock:
        if _db_names_state["path"] != path or now - _db_names_state["at"] > DB_NAMES_TTL:
            fresh: dict[str, str] = {}
            try:
                initialize_database()
                with get_connection() as connection:
                    for row in connection.execute("SELECT stock_code, stock_name FROM stocks"):
                        key, name = str(row["stock_code"]).strip().upper(), str(row["stock_name"] or "").strip()
                        if name and name.upper() != key:
                            fresh[key] = name
            except Exception:  # noqa: BLE001
                logger.warning("讀 stocks 股名失敗，一分鐘後再試", exc_info=True)
            _db_names.clear()
            _db_names.update(fresh)
            _db_names_state.update(path=path, at=now if fresh else now - DB_NAMES_TTL + 60)
        return _db_names.get(code)


def group_and_name(code: str) -> tuple[str, str]:
    if not _group_by_code:
        for name, members in STOCK_GROUPS.items():
            if name in SPECIAL_GROUP_NAMES:
                continue
            for member_code, stock_name in members:
                _group_by_code.setdefault(str(member_code).strip().upper(), (name, str(stock_name)))
    hit = _group_by_code.get(code)
    if hit:
        return hit
    return "", _db_stock_name(str(code).strip().upper()) or code


def in_scan_window(now: datetime) -> bool:
    if not is_trading_day(now):  # 週末、國定假日不開盤
        return False
    minute = now.hour * 60 + now.minute
    return MARKET_OPEN_MINUTE <= minute < MARKET_SCAN_END_MINUTE


def live_score(ma_sums: dict[str, Any], price: float, periods: list[int]) -> int:
    mas = {p: (float(ma_sums[str(p)]) + price) / p for p in periods}
    ordered = sorted(mas)
    return sum(1 for i in range(len(ordered)) for j in range(i + 1, len(ordered)) if mas[ordered[i]] > mas[ordered[j]])


def evaluate_launch(info: dict[str, Any], quote: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any] | None:
    price = quote.get("price")
    if not price or price <= 0 or info.get("skipped"):
        return None
    box_high = float(info.get("boxHigh") or 0)
    if box_high <= 0 or price <= box_high:
        return None
    score = live_score(info["maSums"], price, list(rules["maPeriods"]))
    if score < int(rules["launchMinScore"]):
        return None
    # 2026-10-04 使用者：周轉率／量比直接用盤中實際累積量，不再依已過時間換算成全天預估量（前端同步關掉）；
    # 永久紀錄的 projTurnoverPct 這個 key 沿用，內容是實際累積周轉率。
    volume = float(quote.get("volume") or 0)
    shares = float(info.get("sharesLots") or 0)
    avg5 = float(info.get("avgVol5") or 0)
    proj_turnover = volume / shares * 100 if shares > 0 else None
    vol_ratio = volume / avg5 if avg5 > 0 else None
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
        # 2026-10-02 使用者：永久紀錄的發動列表漲跌幅欄也要能顯示紅底白字（跟即時的發動列表一樣）；
        # EOD收盤回推那條路徑（quote只有price/prevClose/volume）沒有這兩個欄位，quote.get會安全拿到None。
        "limitUp": bool(quote.get("limitUp")),
        "limitDown": bool(quote.get("limitDown")),
    }


def _miss_detail(info: dict[str, Any], quote: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """本來在發動中的股票這一輪為什麼判斷不在發動（給 /scan 狀態查問題用）。"""
    price = float(quote.get("price") or 0)
    box_high = float(info.get("boxHigh") or 0)
    score = live_score(info["maSums"], price, list(rules["maPeriods"])) if price > 0 else None
    volume = float(quote.get("volume") or 0)
    shares = float(info.get("sharesLots") or 0)
    reason = "belowBox" if price <= box_high else "score" if score is not None and score < int(rules["launchMinScore"]) else "volume"
    return {"reason": reason, "price": price, "boxHigh": box_high, "score": score, "volume": volume,
            "turnoverPct": round(volume / shares * 100, 2) if shares > 0 else None,
            "priceSource": quote.get("priceSource"), "quoteTime": quote.get("quoteTime")}


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
            source = "trade"
            if price is None:
                bid, ask = _book(item.get("b")), _book(item.get("a"))
                # 2026-10-05：千附委買委賣平均算出 69.30000000000001（例如 69.2／69.4），比箱頂 69.3 多一點點就被當成過箱頂；
                # 證交所價格最多 4 位小數，平均價四捨五入到 4 位
                price = bid if bid and not ask else ask if ask and not bid else round((bid + ask) / 2, 4) if bid and ask else None
                source = "book"
            if price is None:
                # 沒成交也沒委買委賣：只剩開盤價／最高／最低／昨收可以填，不是現在的價格（scan_once 不拿它判斷回落）
                price = _num(item.get("o")) or _num(item.get("h")) or _num(item.get("l")) or prev_close
                source = "fallback"
            if price is None:
                continue
            day = str(item.get("d") or "")
            # u／w＝當天漲停價／跌停價（跟tw-groups首頁那份報價同一套算法）：發動永久紀錄的漲跌幅欄
            # 要能判斷漲停，不能只看當下這一盤有沒有成交在那個價位。
            limit_up_price = _num(item.get("u"))
            limit_down_price = _num(item.get("w"))
            out[code] = {
                "price": price, "prevClose": prev_close, "volume": int(_num(item.get("v")) or 0),
                "open": _num(item.get("o")), "name": str(item.get("n") or "").strip() or None,
                "quoteDate": f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else None,
                "quoteTime": str(item.get("t") or "") or None,
                "limitUp": limit_up_price is not None and price >= limit_up_price - 1e-6,
                "limitDown": limit_down_price is not None and price <= limit_down_price + 1e-6,
                "priceSource": source,
            }
    return out


GROUP_QUOTES_CACHE_SECONDS = 10
GROUP_QUOTES_MAX_CODES = 600
_group_quotes_cache: dict[str, Any] = {"key": None, "at": 0.0, "payload": None}
_group_quotes_lock = threading.Lock()


def group_quotes_payload(raw_codes: Any, *, fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """證交所即時報價代抓（2026-10-06 使用者：tw-groups 那邊 Cloudflare 抓證交所一直失敗，首頁報價、醞釀／發動、刀劍空全空）。
    worker 自己抓不到時改問這裡：從這台主機抓（跟醞釀／發動掃描同一套 fetch_mis_quotes），每 80 檔一段同時抓；
    同一批代號 10 秒內共用一份（同時進來的請求排隊等同一份，不會各自去打證交所）。全部段落都失敗才丟例外。"""
    seen: list[str] = []
    for token in str(raw_codes or "").replace(" ", ",").split(","):
        code = token.strip().upper()
        if code and 4 <= len(code) <= 6 and code.isalnum() and code not in seen:
            seen.append(code)
    if not seen:
        raise ValueError("沒有代號")
    codes = sorted(seen[:GROUP_QUOTES_MAX_CODES])
    key = ",".join(codes)
    with _group_quotes_lock:
        cached = _group_quotes_cache["payload"]
        if cached is not None and _group_quotes_cache["key"] == key and time.time() - _group_quotes_cache["at"] < GROUP_QUOTES_CACHE_SECONDS:
            return cached
        markets = _markets(codes)
        chunks = [codes[i:i + MIS_CHUNK] for i in range(0, len(codes), MIS_CHUNK)]
        quotes: dict[str, dict[str, Any]] = {}
        failed: list[str] = []
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(6, len(chunks))) as pool:
            futures = [pool.submit(fetch_mis_quotes, chunk, markets, fetcher=fetcher) for chunk in chunks]
            for future in futures:
                try:
                    quotes.update(future.result())
                except Exception as exc:  # noqa: BLE001
                    failed.append(str(exc)[:200])
        if len(failed) == len(chunks):
            raise RuntimeError(failed[0] if failed else "證交所沒有回應")
        out: dict[str, dict[str, Any]] = {}
        latest_date = latest_time = ""
        for code, q in quotes.items():
            price, prev = q.get("price"), q.get("prevClose")
            if not price or not prev:
                continue
            out[code] = {
                "price": price, "prevClose": prev, "change": round(price - prev, 4), "changePercent": (price - prev) / prev * 100,
                "open": q.get("open"), "limitUp": bool(q.get("limitUp")), "limitDown": bool(q.get("limitDown")),
                "volume": q.get("volume"), "name": q.get("name"),
            }
            day, clock = str(q.get("quoteDate") or ""), str(q.get("quoteTime") or "")
            if day > latest_date or (day == latest_date and clock > latest_time):
                latest_date, latest_time = day, clock
        payload = {
            "status": "ok", "quotes": out, "quoteDate": latest_date or None, "quoteTime": latest_time or None,
            "missing": len(codes) - len(out), "failedChunks": len(failed), "fetchedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"),
        }
        _group_quotes_cache.update(key=key, at=time.time(), payload=payload)
        return payload


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

_currently_live: set[str] = set()  # 這一天目前還在發動中的代號（不是「今天發動過」，是「現在還在發動」）
_currently_live_date: str | None = None
# 報價有問題（這一輪沒拿到／只剩開盤價可填／量是 0／收盤試撮／醞釀資料這輪沒這檔）、回落還沒確認、
# 或今天記過又沒看到它回落而照舊算「還在發動」（不記新的一筆）的次數，一天一份，給 /scan 狀態看
CARRY_KEYS = ("missing", "fallback", "noVolume", "auction", "notInPayload", "unconfirmed", "noSeenFall")
_carried_today: dict[str, Any] = {"date": None, **{key: 0 for key in CARRY_KEYS}}
# 2026-10-05：合併第一版修正後，漲停鎖死的聯一光 10:16 還是被記了一筆「重新發動」（報價看起來正常）。
# 要連續 FALL_CONFIRM_POLLS 輪都判斷不在發動才算真的回落，單獨一輪的怪報價不算；
# 本來在發動中的股票每次判斷不在發動的細節留最近幾筆（_recent_misses），從 /scan 狀態看得到是哪一個條件掉的。
FALL_CONFIRM_POLLS = 2
_fall_streak: dict[str, int] = {}
_recent_misses: deque = deque(maxlen=30)
# 2026-10-05：10:52 盤中重新部署後，鼎元、聯一光、萬潤又被記了一筆「重新發動」——程式重開後記憶體是空的，
# 第一輪剛好沒拿到它們的正常報價，基準裡就沒有它們，下一輪報價正常就被當成剛剛才發動。
# 今天已經有紀錄的股票，要這次啟動後親眼看到它確定回落（連續 FALL_CONFIRM_POLLS 輪不在發動）、
# 之後再站上來，才記一筆重新發動；沒看到回落的（重開前就在發動、或重開那幾輪報價怪怪的）只放回發動中。
# 今天還沒有任何紀錄的股票不受影響，第一次發動照樣馬上記（重開機前錯過的也會補上）。
_seen_fall: set[str] = set()
# 2026-10-05：13:25～13:30 收盤前是集合競價，證交所 MIS 這段只揭示試撮的價格／委買委賣，不是真的成交
# （千附收盤前被記了兩筆「重新發動」、茂訊 127.5 這個價格今天根本沒成交過也被記成發動）。
# 報價時間落在這段的不拿來改變發動狀態：本來在發動的照舊、沒在發動的不算新發動；13:30 收盤那一盤再照常判斷。


def _closing_auction_quote(quote: dict[str, Any]) -> bool:
    return CLOSING_AUCTION_START <= str(quote.get("quoteTime") or "") < CLOSING_AUCTION_END


def scan_once(
    *, now: datetime | None = None, payload: dict[str, Any] | None = None,
    quotes_fetcher: Callable[[str], Any] | None = None, quotes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """掃一輪：存醞釀快照（一天一次），盤中判斷發動。同一檔股票今天可以分好幾次發動（發動→回落→再發動）——
    每一輪都重新判斷全部股票現在是不是發動中（不是只查還沒發動過的），跟上一輪比對，從「沒發動」變「發動」
    才算一次新的開始，各自新增一筆紀錄（record_launch_episode，不覆蓋任何一筆舊的，包括第一次那筆）。
    2026-09-29 使用者：程式重開機（部署新版）記憶體會歸零，如果直接把這一輪全部currently-live股票都當
    「剛剛才發動」會灌一堆假紀錄進去；所以「今天已經有紀錄」的股票要這次啟動後看到它確定回落（_seen_fall）
    才會再記一筆，只有「今天完全沒被記過」的股票才直接補一筆（代表真的錯過了它今天的第一次）。"""
    global _currently_live, _currently_live_date
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
    if _currently_live_date != today:
        _currently_live, _currently_live_date = set(), today
        _fall_streak.clear()
        _seen_fall.clear()
    if _carried_today.get("date") != today:
        _carried_today.update({"date": today, **{key: 0 for key in CARRY_KEYS}})
    rules = payload["rules"]
    stocks = {code: info for code, info in (payload.get("stocks") or {}).items() if not info.get("skipped")}
    codes = sorted(stocks)
    if not codes:
        return {"status": "ok", "checked": 0, "launched": 0, "launchedToday": len(launched_codes(today)), "brewSnapshot": snapshot}
    if quotes is None:
        quotes = fetch_mis_quotes(codes, _markets(codes), fetcher=quotes_fetcher)
    live_now: set[str] = set()
    newly_live: list[dict[str, Any]] = []
    carried = {key: 0 for key in CARRY_KEYS}
    for code in _currently_live - set(codes):   # 醞釀資料這一輪剛好沒這檔：不是回落
        live_now.add(code)
        carried["notInPayload"] += 1
    for code in codes:
        quote = quotes.get(code)
        # 2026-10-05 使用者兩台電腦「今天曾發動」對不起來，順便查到後端紀錄灌水：鼎元漲停鎖死一早上記了 10 筆、
        # 9/30 聯合再生一天 56 筆。證交所報價偶爾某一輪沒有這檔、或那一盤沒成交也沒委買委賣（只剩開盤價可填）、
        # 或量是 0——都不是真的回落，但以前直接當成「沒在發動」，下一輪報價正常就又記一筆「重新發動」。
        # 本來在發動中的遇到這種報價照舊算在發動中；本來沒在發動的也不拿這種報價判斷新發動。
        glitch = ("missing" if not quote else "fallback" if quote.get("priceSource") == "fallback"
                  else "noVolume" if not quote.get("volume") else "auction" if _closing_auction_quote(quote) else None)
        if glitch:
            if code in _currently_live:
                live_now.add(code)
                carried[glitch] += 1
            continue
        metrics = evaluate_launch(stocks[code], quote, rules)
        if not metrics:
            streak = min(_fall_streak.get(code, 0) + 1, FALL_CONFIRM_POLLS)
            _fall_streak[code] = streak
            if code in _currently_live:
                _recent_misses.append({"at": now.isoformat(timespec="seconds"), "code": code, "streak": streak,
                                       **_miss_detail(stocks[code], quote, rules)})
            if streak < FALL_CONFIRM_POLLS:
                if code in _currently_live:
                    live_now.add(code)
                    carried["unconfirmed"] += 1
                continue
            _seen_fall.add(code)
            continue
        _fall_streak.pop(code, None)
        live_now.add(code)
        if code not in _currently_live:
            newly_live.append({"code": code, **metrics})
    held_back: set[str] = set()
    if newly_live:
        already_recorded = launched_codes(today)
        held_back = {row["code"] for row in newly_live if row["code"] in already_recorded and row["code"] not in _seen_fall}
        newly_live = [row for row in newly_live if row["code"] not in held_back]
        carried["noSeenFall"] += len(held_back)
    _seen_fall.difference_update(live_now)
    if newly_live:
        # 2026-10-03 使用者：發動永久紀錄（今天曾發動／昨天／前天）也要能顯示盤中大戶力，跟即時
        # 列表、所有族群綜合表同一套；只查這一輪新發動的那幾檔（通常個位數），不是整個排行，
        # 查詢成本很小——跟融資融券/股期那些交易資訊（get_trading_eligibility）同一個做法。
        from main_force_store import load_main_force_ranking

        try:
            holder_by_code = {r["code"]: r for r in load_main_force_ranking(today, codes=[row["code"] for row in newly_live])}
        except Exception:  # noqa: BLE001
            holder_by_code = {}
        for row in newly_live:
            holder = holder_by_code.get(row["code"])
            row["strengthPct"] = holder["strengthPct"] if holder else None
            row["netAmount"] = holder["netAmount"] if holder else None
            row["holderLabel"] = holder["holderLabel"] if holder else None
        record_launch_episode(today, newly_live, now.isoformat(timespec="seconds"))
    _currently_live = live_now
    for key, count in carried.items():
        _carried_today[key] += count
    return {
        "status": "ok", "checked": len(codes), "launched": len(newly_live), "codes": [row["code"] for row in newly_live],
        "launchedToday": len(launched_codes(today)), "currentlyLive": len(live_now), "brewSnapshot": snapshot,
        "carried": carried, "carriedToday": dict(_carried_today), "noSeenFallCodes": sorted(held_back),
    }


def scan_status() -> dict[str, Any]:
    return {"enabled": _enabled(), "pollSeconds": POLL_SECONDS, "scanWindow": "週一～五 09:00～13:35",
            "inWindowNow": in_scan_window(datetime.now(TW_TZ)), "backfillDays": BACKFILL_DAYS,
            "fallConfirmPolls": FALL_CONFIRM_POLLS, "recentMisses": list(_recent_misses)[-15:], **_state}


def _loop() -> None:
    while True:
        now = datetime.now(TW_TZ)
        if _backfill_due(now):
            _run_backfill(now)
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
