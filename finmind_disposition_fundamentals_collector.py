"""處置股預測Phase 2：FinMind Sponsor帳號抓本益比/股價淨值比/市值/融資融券餘額，
只服務43個官方族群股票（不是全市場——全市場~1900檔逐檔打太貴，而且disposition_
prediction.py本來就只對這個範圍跑判定）。沿用finmind_broker_branch_collector.py
的認證/限速/併發模式(RollingHourlyLimiter+ThreadPoolExecutor)，不重新發明一套。

資料集(已用官方文件/llms-full.txt核對過欄位)：
  - TaiwanStockPER：date/stock_id/dividend_yield/PER/PBR（Free tier）
  - TaiwanStockMarketValue：date/stock_id/market_value（Sponsor tier）；跟當天
    bars_1d收盤價一起可以推算發行股數=market_value/close，disposition_rules.py
    的週轉率欄位由呼叫端(finmind_disposition_fundamentals_collector.compute_
    turnover_pct)算好再放進ClauseInputs，不是這裡直接算。
  - TaiwanStockMarginPurchaseShortSale：date/stock_id/MarginPurchaseTodayBalance/
    MarginPurchaseLimit/ShortSaleTodayBalance/ShortSaleLimit等（Sponsor tier）。

全體平均值的注意事項：disposition_market_stats.py用bars_1d算的橫斷面統計是「真的
全市場」(official_daily_bars.py收全部上市櫃)；但本模組收的本益比/淨值比/週轉率/
券資比只涵蓋43個官方族群、524檔，disposition_prediction.py拿這524檔的橫斷面平均
當「全體有價證券」平均值的近似值，不是官方定義的真全市場平均——這是Phase 2資料
收集範圍限制造成的已知近似，不是計算邏輯錯誤。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from daily_bars_store import load_daily_bars
from disposition_fundamentals_store import save_fundamentals_rows, save_margin_short_rows

logger = logging.getLogger("hanstock.finmind_disposition_fundamentals")
TAIPEI = ZoneInfo("Asia/Taipei")
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"


def _token() -> str:
    return os.getenv("FINMIND_TOKEN", "").strip()


class RollingHourlyLimiter:
    """跟finmind_broker_branch_collector.py同一套：Sponsor每小時6,000次，這裡跟
    分點收集器共用同一個FINMIND_TOKEN額度，保留更多餘裕給彼此，上限抓低一點。"""

    def __init__(self, limit: int = 2000, window_seconds: float = 3600.0) -> None:
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
    limit=int(os.getenv("FINMIND_DISPOSITION_SAFE_HOURLY_LIMIT", "2000"))
)


def _request_json(dataset: str, code: str, trade_date: str, *, retries: int = 3) -> list[dict[str, Any]]:
    token = _token()
    if not token:
        raise RuntimeError("FINMIND_TOKEN 尚未設定")
    params = {"dataset": dataset, "data_id": code, "start_date": trade_date, "end_date": trade_date}
    request = urllib.request.Request(
        f"{FINMIND_DATA_URL}?{urllib.parse.urlencode(params)}",
        headers={
            "Accept": "application/json", "Authorization": f"Bearer {token}",
            "User-Agent": "HanStock-FinMind-DispositionCollector/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        _hourly_limiter.acquire()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = json.load(response)
            data = payload.get("data") if isinstance(payload, dict) else None
            return data if isinstance(data, list) else []
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504} or attempt + 1 >= retries:
                raise
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 30.0
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt + 1 >= retries:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(str(last_error or "FinMind request failed"))


def _latest_row(rows: list[dict[str, Any]], trade_date: str) -> dict[str, Any] | None:
    """單日查詢理論上只有一筆，防禦性地挑date完全對得上的那筆（避免FinMind偶爾多回
    鄰近日期造成用錯值）。"""
    for row in rows:
        if str(row.get("date"))[:10] == trade_date:
            return row
    return rows[-1] if rows else None


def fetch_per_pbr(code: str, trade_date: str) -> dict[str, float | None] | None:
    rows = _request_json("TaiwanStockPER", code, trade_date)
    row = _latest_row(rows, trade_date)
    if row is None:
        return None
    return {
        "peRatio": row.get("PER"), "pbr": row.get("PBR"), "dividendYield": row.get("dividend_yield"),
    }


def fetch_market_value(code: str, trade_date: str) -> float | None:
    rows = _request_json("TaiwanStockMarketValue", code, trade_date)
    row = _latest_row(rows, trade_date)
    return row.get("market_value") if row else None


def fetch_margin_short(code: str, trade_date: str) -> dict[str, float | None] | None:
    rows = _request_json("TaiwanStockMarginPurchaseShortSale", code, trade_date)
    row = _latest_row(rows, trade_date)
    if row is None:
        return None
    return {
        "marginTodayBalance": row.get("MarginPurchaseTodayBalance"),
        "marginLimit": row.get("MarginPurchaseLimit"),
        "shortTodayBalance": row.get("ShortSaleTodayBalance"),
        "shortLimit": row.get("ShortSaleLimit"),
    }


def _collect_one_stock(code: str, trade_date: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """回傳(fundamentals_row, margin_short_row)；任一資料集失敗不影響另一個，個別
    設為None，呼叫端存的時候該欄位自然是NULL(對應disposition_rules.py的「缺資料
    當作不成立」原則)。"""
    fundamentals_row: dict[str, Any] | None = None
    margin_row: dict[str, Any] | None = None
    try:
        per_pbr = fetch_per_pbr(code, trade_date)
        market_value = fetch_market_value(code, trade_date)
        if per_pbr is not None or market_value is not None:
            fundamentals_row = {
                "code": code, "tradeDate": trade_date,
                "peRatio": (per_pbr or {}).get("peRatio"), "pbr": (per_pbr or {}).get("pbr"),
                "dividendYield": (per_pbr or {}).get("dividendYield"), "marketValue": market_value,
            }
    except Exception as error:  # noqa: BLE001
        logger.warning("[DispositionFundamentals] %s 本益比/市值抓取失敗: %s", code, error)
    try:
        margin_short = fetch_margin_short(code, trade_date)
        if margin_short is not None:
            margin_row = {"code": code, "tradeDate": trade_date, **margin_short}
    except Exception as error:  # noqa: BLE001
        logger.warning("[DispositionFundamentals] %s 融資融券抓取失敗: %s", code, error)
    return fundamentals_row, margin_row


FINMIND_UNAVAILABLE_CODES = {401, 402, 403}


def _finmind_unavailable_reason(code: str, trade_date: str) -> str | None:
    """整批開打前先試一檔：FinMind 回 401／402／403（token 失效、付費方案到期、IP 被封）就整批跳過，
    不要 391 檔 × 3 個資料集一起打（2026-09-23 方案到期後就是這樣一直打，打到 IP 被封）。
    其他錯誤（逾時、單檔沒資料）照舊逐檔處理。"""
    try:
        fetch_per_pbr(code, trade_date)
    except urllib.error.HTTPError as error:
        if error.code in FINMIND_UNAVAILABLE_CODES:
            return f"HTTP {error.code}: {error.reason}"
    except Exception:  # noqa: BLE001
        return None
    return None


def collect_trade_date(trade_date: str, codes: list[str], *, workers: int = 12) -> dict[str, Any]:
    if not _token():
        return {"status": "skipped", "reason": "FINMIND_TOKEN 未設定", "tradeDate": trade_date}
    reason = _finmind_unavailable_reason(codes[0], trade_date) if codes else None
    if reason:
        return {"status": "skipped", "reason": f"FinMind 無法使用（{reason}），這次整批跳過", "tradeDate": trade_date}
    fundamentals_rows: list[dict[str, Any]] = []
    margin_rows: list[dict[str, Any]] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="finmind-disposition") as pool:
        future_map = {pool.submit(_collect_one_stock, code, trade_date): code for code in codes}
        for future in as_completed(future_map):
            code = future_map[future]
            try:
                fundamentals_row, margin_row = future.result()
            except Exception as error:  # noqa: BLE001
                failures.append(f"{code}:{error}")
                continue
            if fundamentals_row:
                fundamentals_rows.append(fundamentals_row)
            if margin_row:
                margin_rows.append(margin_row)
    saved_fundamentals = save_fundamentals_rows(fundamentals_rows)
    saved_margin = save_margin_short_rows(margin_rows)
    return {
        "status": "ok", "tradeDate": trade_date, "requested": len(codes),
        "savedFundamentals": saved_fundamentals, "savedMarginShort": saved_margin,
        "failed": len(failures), "failedSample": failures[:5],
    }
