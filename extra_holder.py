"""族群表以外股票的收盤後大戶力（2026-11-15 使用者：首頁精材、敬鵬、營邦、騰雲…不在 67 個族群裡，盤中沒訂逐筆，大戶力一直是「—」）。

逐筆訂閱只有 586 檔的空間，族群股已經用掉 579 檔，使用者選「先收盤後補算」：
收盤後用 Shioaji 歷史逐筆（跟 big_holder.backfill_day 同一套 stock_bar_bootstrap 工具）算整天的老師版大戶淨額
（同秒同方向併單 ≥7 張大戶買 − 大戶賣，開盤第一筆算賣）÷ 近 5 日平均成交值 × 100，跟首頁大戶力排行同一個公式。

- 只算族群表以外的（族群股盤中本來就有）；一次最多 MAX_CODES 檔。
- 交易日 13:36 以後才算今天（逐筆要收完）；之前算上一個有資料的交易日。
- 算過的存資料表，同一天同一檔只算一次（抓不到逐筆、合約找不到也記下來，不重複打永豐）。
- 前端送「畫面上缺大戶力的股票」來問，有算好的就回，沒算好的排進背景佇列慢慢算（每檔之間隔一下，歷史流量額度不夠就停）。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dt_time
from typing import Any, Iterable, Optional

import database
from otc_index import TW_TZ

logger = logging.getLogger("hanstock.extra_holder")

MAX_CODES = 150
READY_AFTER = dt_time(13, 36)
PAUSE_SECONDS = 0.4
RESERVE_BYTES = 100_000_000

_table_lock = threading.Lock()
_table_ready_path: Optional[str] = None
_queue_lock = threading.Lock()
_queue: list[tuple[str, str]] = []
_queued: set[tuple[str, str]] = set()
_worker: Optional[threading.Thread] = None
_status: dict[str, Any] = {"state": "idle", "done": 0, "errors": 0, "lastError": None}


def _ensure_table() -> None:
    global _table_ready_path
    path = str(database.DATABASE_PATH.resolve())
    if _table_ready_path == path:
        return
    with _table_lock:
        if _table_ready_path == path:
            return
        with database.get_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS extra_holder (
                    trade_date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    net_amount REAL,
                    avg_amount REAL,
                    total_amount REAL,
                    pct REAL,
                    error TEXT,
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (trade_date, stock_code)
                )
                """
            )
        _table_ready_path = path


def _is_trading_day(day: Any) -> bool:
    if day.weekday() >= 5:
        return False
    try:
        from trading_days import is_trading_day

        return bool(is_trading_day(day))
    except Exception:  # noqa: BLE001 — 休市日表讀不到就照平日處理
        return True


def target_date(now: Optional[datetime] = None) -> Optional[str]:
    """要補哪一天：交易日 13:36 以後＝今天；其他時候＝今天以前最近一個有大戶力資料的交易日。"""
    current = (now or datetime.now(TW_TZ)).astimezone(TW_TZ)
    today = current.strftime("%Y-%m-%d")
    if _is_trading_day(current.date()) and current.time().replace(tzinfo=None) >= READY_AFTER:
        return today
    from main_force_store import latest_trade_date_with_data

    return latest_trade_date_with_data(today)


def compute_one(service: Any, code: str, trade_date: str) -> dict[str, Any]:
    """一檔一天：歷史逐筆 → 大戶淨額、當日成交額 → 大戶力%（近 5 日均成交值算不出來就 None）。"""
    from big_holder import avg_turnover
    from main_force_store import classify_big_holder
    from stock_bar_bootstrap import _fetch_historical_ticks, _historical_tick_metrics, _resolve_stock_contract

    api = getattr(service, "api", None)
    contract = _resolve_stock_contract(service, code)
    if contract is None:
        return {"error": "找不到股票合約"}
    metrics = _historical_tick_metrics(_fetch_historical_ticks(api, contract, trade_date), trade_date=trade_date)
    if not metrics:
        return {"error": "那天沒有逐筆成交"}
    net = sum(float(row.get("big_buy_amount") or 0) - float(row.get("big_sell_amount") or 0) for row in metrics.values())
    total = sum(float(row.get("total_amount") or 0) for row in metrics.values())
    avg = avg_turnover(code, trade_date)
    pct, _label = classify_big_holder(net, avg, total)
    return {"net": net, "avg": avg, "total": total, "pct": pct}


def _save(trade_date: str, code: str, result: dict[str, Any]) -> None:
    _ensure_table()
    with database.get_connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO extra_holder (trade_date, stock_code, net_amount, avg_amount, total_amount, pct, error, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_date, code, result.get("net"), result.get("avg"), result.get("total"), result.get("pct"), result.get("error"),
             datetime.now(TW_TZ).isoformat(timespec="seconds")),
        )


def load(trade_date: str, codes: Iterable[str]) -> dict[str, dict[str, Any]]:
    wanted = sorted({str(c).strip().upper() for c in codes if str(c).strip()})
    if not wanted:
        return {}
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM extra_holder WHERE trade_date = ? AND stock_code IN ({', '.join('?' for _ in wanted)})",
            (trade_date, *wanted),
        ).fetchall()
    return {row["stock_code"]: {"pct": row["pct"], "net": None if row["net_amount"] is None else round(row["net_amount"]),
                                "error": row["error"]} for row in rows}


def _run_queue(service_getter: Any, quota_check: Any) -> None:
    global _worker
    try:
        while True:
            with _queue_lock:
                if not _queue:
                    break
                trade_date, code = _queue.pop(0)
            service = service_getter()
            api = getattr(service, "api", None)
            blocker = None
            if api is None or not bool(getattr(getattr(service, "state", None), "logged_in", False)):
                blocker = "Shioaji 尚未登入"
            elif quota_check is not None:
                blocker = quota_check(api) or None
            if blocker:                       # 還沒登入／額度不夠：這輪先停，下次有人來問再排（不記成錯誤，免得之後都不算）
                _status.update(state="paused", lastError=str(blocker))
                with _queue_lock:
                    _queue.clear()
                    _queued.clear()
                return
            try:
                _save(trade_date, code, compute_one(service, code, trade_date))
                _status["done"] += 1
            except Exception as exc:  # noqa: BLE001 — 單檔失敗記下來，不重複打
                logger.warning("extra_holder %s %s 失敗：%s", code, trade_date, exc)
                _save(trade_date, code, {"error": str(exc) or exc.__class__.__name__})
                _status["errors"] += 1
            finally:
                with _queue_lock:
                    _queued.discard((trade_date, code))
            time.sleep(PAUSE_SECONDS)
        _status["state"] = "idle"
    finally:
        with _queue_lock:
            _worker = None


def enqueue(trade_date: str, codes: Iterable[str], *, service_getter: Any = None, quota_check: Any = None) -> int:
    """排進背景佇列（已經在排的不重複排），需要的話開背景執行緒；回傳這次新排幾檔。"""
    global _worker
    added = 0
    with _queue_lock:
        for code in codes:
            key = (trade_date, code)
            if key in _queued:
                continue
            _queued.add(key)
            _queue.append(key)
            added += 1
        if _queue and _worker is None:
            if service_getter is None:
                from quote_service import get_quote_service as service_getter  # noqa: N813
            if quota_check is None:
                from history_quota import history_quota

                def quota_check(api: Any) -> Optional[str]:   # 背景補算：留 100MB 歷史額度給開圖、校正這些即時需求
                    return history_quota.check_background(api, reserve_bytes=RESERVE_BYTES)
            _status["state"] = "running"
            _worker = threading.Thread(target=_run_queue, args=(service_getter, quota_check), name="extra-holder", daemon=True)
            _worker.start()
    return added


def payload(codes: Iterable[str], *, now: Optional[datetime] = None, universe: Optional[set[str]] = None,
            service_getter: Any = None, quota_check: Any = None) -> dict[str, Any]:
    wanted = list(dict.fromkeys(str(c).strip().upper() for c in codes if str(c).strip()))
    if len(wanted) > MAX_CODES:
        raise ValueError(f"一次最多 {MAX_CODES} 檔")
    if universe is None:
        from stock_groups import industry_group_codes

        universe = set(industry_group_codes())
    wanted = [c for c in wanted if c not in universe]          # 族群股盤中本來就有大戶力
    date = target_date(now)
    if not date or not wanted:
        return {"status": "ok", "tradeDate": date, "items": {}, "pending": 0, "worker": dict(_status)}
    done = load(date, wanted)
    missing = [c for c in wanted if c not in done]
    if missing:
        enqueue(date, missing, service_getter=service_getter, quota_check=quota_check)
    return {
        "status": "ok",
        "tradeDate": date,
        "formula": "收盤後用歷史逐筆補算：同秒同方向併單 ≥7 張大戶買 − 大戶賣（開盤第一筆算賣）÷ 近 5 日平均成交值 × 100",
        "items": {code: row for code, row in done.items() if row.get("pct") is not None},
        "failed": {code: row["error"] for code, row in done.items() if row.get("error")},
        "pending": len(missing),
        "worker": dict(_status),
    }
