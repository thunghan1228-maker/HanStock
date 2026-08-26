"""FinMind Sponsor 主動式 ETF 每日持股異動彙整。"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockActiveETFHoldingChange"
TAIPEI = ZoneInfo("Asia/Taipei")
_lock = threading.RLock()


def _token() -> str:
    token = os.getenv("FINMIND_TOKEN", "").strip()
    if not token:
        raise RuntimeError("FINMIND_TOKEN 尚未設定")
    return token


def _cache_root() -> Path:
    root = Path(os.getenv("HANSTOCK_DATA_DIR", "/data")) / "finmind_active_etf"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_cached(day: str) -> list[dict[str, Any]] | None:
    path = _cache_root() / f"{day}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_cached(day: str, rows: list[dict[str, Any]]) -> None:
    path = _cache_root() / f"{day}.json"
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    pending.replace(path)


def _fetch_day(day: str) -> list[dict[str, Any]]:
    with _lock:
        cached = _read_cached(day)
        if cached is not None:
            return cached
        request = Request(
            f"{FINMIND_DATA_URL}?{urlencode({'dataset': DATASET, 'start_date': day})}",
            headers={"Authorization": f"Bearer {_token()}", "User-Agent": "HanStock/1.4"},
        )
        try:
            with urlopen(request, timeout=45) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"FinMind ETF API HTTP {exc.code}") from exc
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError(str(payload.get("msg") if isinstance(payload, dict) else "FinMind ETF 回傳格式錯誤"))
        # FinMind 未給 end_date 時可能回傳 start_date 之後的多日資料；
        # 每個快取檔只能保存指定交易日，避免五日統計重複計入。
        clean = [
            row for row in rows
            if isinstance(row, dict) and str(row.get("date") or "")[:10] == day
        ]
        # 當日資料盤後才發布；空回應不可永久快取，否則收盤後仍會一直
        # 顯示前一日。已有內容的交易日才落盤，並由 Railway Volume 保存。
        if clean:
            _write_cached(day, clean)
        return clean


def _percentile_score(values: dict[str, int], ticker: str) -> float:
    value = values.get(ticker, 0)
    if value == 0:
        return 0.0
    ordered = sorted(values.values())
    if len(ordered) < 2:
        return 100.0 if value > 0 else -100.0
    below = sum(item < value for item in ordered)
    equal = sum(item == value for item in ordered)
    percentile = (below + (equal - 1) / 2) / (len(ordered) - 1)
    return round(-100 + percentile * 200, 1)


def active_etf_flow_for_ticker(ticker: str, trading_days: int = 5) -> dict[str, Any]:
    code = ticker.strip().upper()
    if not code:
        raise ValueError("ticker 不可為空")
    day_rows: list[tuple[str, list[dict[str, Any]]]] = []
    last_error: str | None = None
    for offset in range(0, 18):
        day = (datetime.now(TAIPEI).date() - timedelta(days=offset)).isoformat()
        try:
            rows = _fetch_day(day)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            continue
        if rows:
            day_rows.append((day, rows))
        if len(day_rows) >= trading_days:
            break
    if not day_rows:
        raise RuntimeError(last_error or "尚未取得主動式 ETF 持股異動")

    totals: dict[str, int] = {}
    selected_daily: list[dict[str, Any]] = []
    selected_etfs: set[str] = set()
    for day, rows in day_rows[:trading_days]:
        daily_by_ticker: dict[str, int] = {}
        daily_etfs: dict[str, set[str]] = {}
        for row in rows:
            component = str(row.get("component_stock_id") or "").strip().upper()
            if not component:
                continue
            net = int(float(row.get("buy") or 0)) - int(float(row.get("sell") or 0))
            daily_by_ticker[component] = daily_by_ticker.get(component, 0) + net
            if net:
                daily_etfs.setdefault(component, set()).add(str(row.get("stock_id") or "").strip().upper())
        for component, net in daily_by_ticker.items():
            totals[component] = totals.get(component, 0) + net
        selected_etfs.update(daily_etfs.get(code, set()))
        selected_daily.append({"date": day, "netShares": daily_by_ticker.get(code, 0)})

    five_day_net = totals.get(code, 0)
    latest_net = selected_daily[0]["netShares"] if selected_daily else 0
    score = _percentile_score(totals, code)
    return {
        "ok": True,
        "ticker": code,
        "dataDate": day_rows[0][0],
        "tradingDays": len(day_rows[:trading_days]),
        "latestNetShares": latest_net,
        "fiveDayNetShares": five_day_net,
        "latestNetLots": round(latest_net / 1000, 1),
        "fiveDayNetLots": round(five_day_net / 1000, 1),
        "etfCount": len(selected_etfs),
        "score": score,
        "label": "主動式 ETF 加碼" if score > 15 else "主動式 ETF 減碼" if score < -15 else "主動式 ETF 中性",
        "daily": selected_daily,
        "source": "FinMind TaiwanStockActiveETFHoldingChange",
    }
