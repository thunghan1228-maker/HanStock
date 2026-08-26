"""FinMind Sponsor 券商分點盤後收集器。

只保存 HanStock 計分需要的每日彙總，不保存或對外提供原始分點逐筆資料。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from broker_branch_weekly import save_broker_branch_daily, stored_broker_branch_dates


logger = logging.getLogger("hanstock.finmind_broker_branch")
TAIPEI = ZoneInfo("Asia/Taipei")
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_BRANCH_URL = (
    "https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report"
)
BROKER_BRANCH_SOURCE = "FinMind-derived/TWSE-TPEx-v2"
_CODE_PATTERN = re.compile(r"^[0-9]{4,5}[A-Z]?$", re.IGNORECASE)
_runtime_lock = threading.RLock()
_start_lock = threading.Lock()
_collector_started = False
_universe_cache: tuple[float, list[str]] = (0.0, [])
_runtime: dict[str, Any] = {
    "running": False,
    "currentDate": None,
    "lastStartedAt": None,
    "lastSuccessAt": None,
    "lastError": None,
    "stockCount": 0,
    "fetchedCount": 0,
    "failedCount": 0,
    "savedCount": 0,
    "apiCalls": 0,
}


def _enabled() -> bool:
    return os.getenv(
        "HANSTOCK_FINMIND_BROKER_COLLECTOR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}


def _token() -> str:
    return os.getenv("FINMIND_TOKEN", "").strip()


def _now_iso() -> str:
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


def broker_branch_collection_allowed(now: datetime | None = None) -> bool:
    """分點是盤後資料；交易日盤中禁止啟動全市場 2,000 多檔補抓。"""
    current = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    if current.weekday() >= 5:
        return True
    minutes = current.hour * 60 + current.minute
    return minutes < 8 * 60 + 30 or minutes >= 15 * 60 + 10


def _set_runtime(**values: Any) -> None:
    with _runtime_lock:
        _runtime.update(values)


class RollingHourlyLimiter:
    """Sponsor 每小時 6,000 次，保留少量額度給狀態與日期查詢。"""

    def __init__(self, limit: int = 5700, window_seconds: float = 3600.0) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.limit:
                    self._calls.append(now)
                    return
                wait_seconds = self.window_seconds - (now - self._calls[0]) + 0.25
            time.sleep(max(0.25, wait_seconds))


_hourly_limiter = RollingHourlyLimiter(
    limit=int(os.getenv("FINMIND_SPONSOR_SAFE_HOURLY_LIMIT", "5700"))
)


def _request_json(url: str, params: dict[str, str], *, retries: int = 3) -> dict[str, Any]:
    token = _token()
    if not token:
        raise RuntimeError("FINMIND_TOKEN 尚未設定")
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "HanStock-FinMind-BrokerCollector/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        _hourly_limiter.acquire()
        with _runtime_lock:
            _runtime["apiCalls"] = int(_runtime.get("apiCalls") or 0) + 1
        try:
            with urllib.request.urlopen(request, timeout=35) as response:  # noqa: S310
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise RuntimeError("FinMind 回傳格式不是物件")
            status = payload.get("status")
            if status not in {None, 200, "200"}:
                raise RuntimeError(str(payload.get("msg") or f"FinMind status={status}"))
            return payload
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504} or attempt + 1 >= retries:
                raise
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 65.0
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt + 1 >= retries:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(str(last_error or "FinMind request failed"))


def _latest_stock_universe(rows: list[dict[str, Any]]) -> list[str]:
    latest: dict[str, dict[str, Any]] = {}
    for item in rows:
        code = str(item.get("stock_id") or "").strip().upper()
        if not _CODE_PATTERN.fullmatch(code):
            continue
        item_date = str(item.get("date") or "")
        previous = latest.get(code)
        if previous is None or item_date >= str(previous.get("date") or ""):
            latest[code] = item

    valid_dates = [str(item.get("date") or "")[:10] for item in latest.values() if item.get("date")]
    newest_date = max(valid_dates) if valid_dates else ""
    active_cutoff = (
        (datetime.strptime(newest_date, "%Y-%m-%d").date() - timedelta(days=10)).isoformat()
        if newest_date
        else ""
    )
    result: list[str] = []
    for code, item in latest.items():
        market = str(item.get("type") or "").strip().lower()
        category = str(item.get("industry_category") or "").strip().lower()
        name = str(item.get("stock_name") or "").strip().lower()
        if market not in {"twse", "tpex"}:
            continue
        if active_cutoff and str(item.get("date") or "")[:10] < active_cutoff:
            continue
        if "etn" in category or "權證" in category or "warrant" in category:
            continue
        if "etn" in name or "權證" in name:
            continue
        result.append(code)
    return sorted(set(result))


def fetch_stock_universe(*, force: bool = False) -> list[str]:
    global _universe_cache
    cached_at, cached = _universe_cache
    if cached and not force and time.time() - cached_at < 12 * 3600:
        return list(cached)
    payload = _request_json(FINMIND_DATA_URL, {"dataset": "TaiwanStockInfo"})
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    universe = _latest_stock_universe(rows)
    if not universe:
        raise RuntimeError("FinMind TaiwanStockInfo 未取得上市櫃股票")
    _universe_cache = (time.time(), universe)
    return list(universe)


def fetch_latest_trade_dates(days: int = 5) -> list[str]:
    today = datetime.now(TAIPEI).date()
    payload = _request_json(
        FINMIND_DATA_URL,
        {
            "dataset": "TaiwanStockPrice",
            "data_id": "2330",
            "start_date": (today - timedelta(days=24)).isoformat(),
            "end_date": (today + timedelta(days=1)).isoformat(),
        },
    )
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    dates = sorted({str(item.get("date"))[:10] for item in rows if item.get("date")})
    return dates[-max(1, days):]


def aggregate_branch_rows(
    stock_code: str,
    trade_date: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """轉為每日計分摘要；集中度為同方向前五大分點淨額占比。"""
    by_branch: dict[str, float] = {}
    by_branch_shares: dict[str, float] = {}
    for item in rows:
        branch = str(item.get("securities_trader_id") or item.get("securities_trader") or "").strip()
        if not branch:
            continue
        try:
            price = float(item.get("price") or 0)
            buy = float(item.get("buy") or 0)
            sell = float(item.get("sell") or 0)
        except (TypeError, ValueError):
            continue
        by_branch[branch] = by_branch.get(branch, 0.0) + price * (buy - sell)
        by_branch_shares[branch] = by_branch_shares.get(branch, 0.0) + buy - sell

    # 全市場所有分點的買進與賣出互為交易對手，若把全部分點淨額直接
    # 相加，理論上必然接近 0，不能代表主力方向。改以買方前五大分點
    # 與賣方前五大分點的金額差，作為可比較的「主力分點淨額」。
    positive_branches = sorted(
        ((amount, by_branch_shares[branch]) for branch, amount in by_branch.items() if amount > 0),
        key=lambda item: item[0], reverse=True,
    )
    negative_branches = sorted(
        ((-amount, -by_branch_shares[branch]) for branch, amount in by_branch.items() if amount < 0),
        key=lambda item: item[0], reverse=True,
    )
    positive = [amount for amount, _shares in positive_branches]
    negative = [amount for amount, _shares in negative_branches]
    top_buy_amount = sum(positive[:5])
    top_sell_amount = sum(negative[:5])
    top_buy_shares = sum(shares for _amount, shares in positive_branches[:5])
    top_sell_shares = sum(shares for _amount, shares in negative_branches[:5])
    net_amount = top_buy_amount - top_sell_amount
    net_lots = (top_buy_shares - top_sell_shares) / 1000
    if net_amount >= 0:
        directional = positive
    else:
        directional = negative
    directional_total = sum(directional)
    concentration = (
        sum(directional[:5]) / directional_total * 100 if directional_total > 0 else 0.0
    )
    return {
        "ticker": stock_code,
        "tradeDate": trade_date,
        "netAmount": round(net_amount, 2),
        "netLots": round(net_lots, 3),
        "concentration": round(min(100.0, max(0.0, concentration)), 4),
        "activeBranches": len(by_branch),
        "source": BROKER_BRANCH_SOURCE,
    }


def _fetch_stock_day(stock_code: str, trade_date: str) -> dict[str, Any]:
    payload = _request_json(
        FINMIND_BRANCH_URL,
        {"data_id": stock_code, "date": trade_date},
    )
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    return aggregate_branch_rows(stock_code, trade_date, rows)


def has_meaningful_day_coverage(summaries: list[dict[str, Any]], stock_count: int) -> bool:
    """防止 FinMind 尚未發布資料時，把全市場空回應當成正式零值日。"""
    minimum_active = max(1, int(max(1, stock_count) * 0.5))
    active_count = sum(1 for row in summaries if int(row.get("activeBranches") or 0) > 0)
    return active_count >= minimum_active


def collect_trade_date(trade_date: str, stock_codes: list[str] | None = None) -> dict[str, Any]:
    datetime.strptime(trade_date, "%Y-%m-%d")
    universe = list(stock_codes or fetch_stock_universe())
    workers = max(1, min(32, int(os.getenv("FINMIND_BROKER_WORKERS", "18"))))
    _set_runtime(
        running=True,
        currentDate=trade_date,
        lastStartedAt=_now_iso(),
        lastError=None,
        stockCount=len(universe),
        fetchedCount=0,
        failedCount=0,
        savedCount=0,
    )
    summaries: list[dict[str, Any]] = []
    failures: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="finmind-branch") as pool:
            future_map = {
                pool.submit(_fetch_stock_day, code, trade_date): code for code in universe
            }
            for future in as_completed(future_map):
                code = future_map[future]
                try:
                    summaries.append(future.result())
                except Exception as error:  # noqa: BLE001
                    failures.append(f"{code}:{error}")
                _set_runtime(fetchedCount=len(summaries), failedCount=len(failures))

        maximum_failures = max(10, int(len(universe) * 0.01))
        if len(failures) > maximum_failures:
            raise RuntimeError(
                f"{trade_date} 分點下載失敗 {len(failures)}/{len(universe)}，未寫入不完整資料"
            )
        if not has_meaningful_day_coverage(summaries, len(universe)):
            active_count = sum(1 for row in summaries if int(row.get("activeBranches") or 0) > 0)
            raise RuntimeError(
                f"{trade_date} FinMind 分點尚未完整發布（有效 {active_count}/{len(universe)}），不寫入零值占位資料"
            )
        saved = save_broker_branch_daily(summaries)
        _set_runtime(
            running=False,
            currentDate=None,
            lastSuccessAt=_now_iso(),
            savedCount=saved,
            lastError=("；".join(failures[:3]) if failures else None),
        )
        return {
            "tradeDate": trade_date,
            "stockCount": len(universe),
            "saved": saved,
            "failed": len(failures),
        }
    except Exception as error:
        _set_runtime(running=False, currentDate=None, lastError=str(error))
        raise


def collect_missing_latest_days(days: int = 5) -> dict[str, Any]:
    targets = fetch_latest_trade_dates(days)
    # 只把目前公式版本的日期視為已完成。公式升版時會自動重抓五日，
    # 以相同主鍵覆寫舊摘要，不需要保存或公開原始分點逐筆資料。
    existing = set(stored_broker_branch_dates(limit=40, source=BROKER_BRANCH_SOURCE, require_net_lots=True))
    # 先補最近交易日，讓日排行優先取得精確張數；其餘四日再依序補齊週資料。
    missing = sorted((trade_date for trade_date in targets if trade_date not in existing), reverse=True)
    results = []
    universe = fetch_stock_universe()
    for trade_date in missing:
        results.append(collect_trade_date(trade_date, universe))
    return {"targets": targets, "missing": missing, "results": results}


def finmind_broker_collector_status() -> dict[str, Any]:
    with _runtime_lock:
        runtime = dict(_runtime)
    return {
        "enabled": _enabled(),
        "tokenConfigured": bool(_token()),
        **runtime,
    }


def _collector_loop() -> None:
    startup_delay = max(0, int(os.getenv("FINMIND_BROKER_STARTUP_DELAY_SECONDS", "30")))
    if startup_delay:
        time.sleep(startup_delay)
    while True:
        try:
            if _enabled() and _token() and broker_branch_collection_allowed():
                _set_runtime(pausedReason=None)
                collect_missing_latest_days(5)
            elif _enabled() and _token():
                _set_runtime(running=False, currentDate=None, pausedReason="market_hours")
        except Exception as error:  # noqa: BLE001
            logger.exception("FinMind 券商分點收集失敗")
            _set_runtime(running=False, currentDate=None, lastError=str(error))
        # 只在非盤中時段補資料；之後每 15 分鐘確認，已有日期不會重抓全市場。
        time.sleep(max(300, int(os.getenv("FINMIND_BROKER_CHECK_SECONDS", "900"))))


def start_finmind_broker_branch_collector() -> bool:
    global _collector_started
    if not _enabled() or not _token():
        return False
    with _start_lock:
        if _collector_started:
            return True
        thread = threading.Thread(
            target=_collector_loop,
            name="hanstock-finmind-broker-collector",
            daemon=True,
        )
        thread.start()
        _collector_started = True
    return True
