"""FinMind Sponsor 主動式 ETF 每日持股與異動雷達。"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
CHANGE_DATASET = "TaiwanStockActiveETFHoldingChange"
HOLDING_DATASET = "TaiwanStockActiveETFHolding"
# 舊名稱保留給既有匯入者與測試使用。
DATASET = CHANGE_DATASET
TAIPEI = ZoneInfo("Asia/Taipei")
_lock = threading.RLock()


def _is_taiwan_stock_component(component: str, etf: str) -> bool:
    """雷達只收台灣股票；排除債券 D 型 ETF、海外 ticker 與債券 CUSIP。"""
    return etf.endswith("A") and re.fullmatch(r"\d{4,6}[A-Z]?", component) is not None


def _token() -> str:
    token = os.getenv("FINMIND_TOKEN", "").strip()
    if not token:
        raise RuntimeError("FINMIND_TOKEN 尚未設定")
    return token


def _cache_root(kind: str = "change") -> Path:
    folder = "finmind_active_etf_holding" if kind == "holding" else "finmind_active_etf"
    root = Path(os.getenv("HANSTOCK_DATA_DIR", "/data")) / folder
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_cached(day: str, kind: str = "change") -> list[dict[str, Any]] | None:
    path = _cache_root(kind) / f"{day}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_cached(day: str, rows: list[dict[str, Any]], kind: str = "change") -> None:
    path = _cache_root(kind) / f"{day}.json"
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    pending.replace(path)


def _fetch_dataset_day(day: str, dataset: str, kind: str) -> list[dict[str, Any]]:
    with _lock:
        cached = _read_cached(day, kind)
        if cached is not None:
            return cached
        request = Request(
            f"{FINMIND_DATA_URL}?{urlencode({'dataset': dataset, 'start_date': day})}",
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
            _write_cached(day, clean, kind)
        return clean


def _fetch_day(day: str) -> list[dict[str, Any]]:
    return _fetch_dataset_day(day, CHANGE_DATASET, "change")


def _fetch_holdings_day(day: str) -> list[dict[str, Any]]:
    return _fetch_dataset_day(day, HOLDING_DATASET, "holding")


def _recent_trading_days(
    trading_days: int,
    end_date: str | None = None,
) -> tuple[list[tuple[str, list[dict[str, Any]]]], str | None]:
    anchor = Date.fromisoformat(end_date) if end_date else datetime.now(TAIPEI).date()
    day_rows: list[tuple[str, list[dict[str, Any]]]] = []
    last_error: str | None = None
    for offset in range(0, max(18, trading_days * 4)):
        day = (anchor - timedelta(days=offset)).isoformat()
        try:
            rows = _fetch_day(day)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            continue
        if rows:
            day_rows.append((day, rows))
        if len(day_rows) >= trading_days:
            break
    return day_rows, last_error


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


def active_etf_flow_for_ticker(
    ticker: str,
    trading_days: int = 5,
    end_date: str | None = None,
) -> dict[str, Any]:
    code = ticker.strip().upper()
    if not code:
        raise ValueError("ticker 不可為空")
    day_rows, last_error = _recent_trading_days(trading_days, end_date)
    if not day_rows:
        raise RuntimeError(last_error or "尚未取得主動式 ETF 持股異動")

    totals: dict[str, int] = {}
    selected_daily: list[dict[str, Any]] = []
    selected_etfs: set[str] = set()
    for day, rows in day_rows[:trading_days]:
        daily_by_ticker: dict[str, int] = {}
        daily_etfs: dict[str, dict[str, int]] = {}
        for row in rows:
            component = str(row.get("component_stock_id") or "").strip().upper()
            if not component:
                continue
            net = int(float(row.get("buy") or 0)) - int(float(row.get("sell") or 0))
            daily_by_ticker[component] = daily_by_ticker.get(component, 0) + net
            if net:
                etf = str(row.get("stock_id") or "").strip().upper()
                daily_etfs.setdefault(component, {})[etf] = daily_etfs.setdefault(component, {}).get(etf, 0) + net
        for component, net in daily_by_ticker.items():
            totals[component] = totals.get(component, 0) + net
        selected_etfs.update(daily_etfs.get(code, {}).keys())
        selected_daily.append({
            "date": day,
            "netShares": daily_by_ticker.get(code, 0),
            "etfs": [
                {"code": etf, "netShares": net, "netLots": round(net / 1000, 1)}
                for etf, net in sorted(daily_etfs.get(code, {}).items())
            ],
        })

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
        "disclaimer": "持股股數變化包含申購贖回影響，不等同經理人主動買賣純額。",
    }


def _etf_breakdown(rows: list[dict[str, Any]], component: str) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for row in rows:
        if str(row.get("component_stock_id") or "").strip().upper() != component:
            continue
        etf = str(row.get("stock_id") or "").strip().upper()
        if not _is_taiwan_stock_component(component, etf):
            continue
        net = int(float(row.get("buy") or 0)) - int(float(row.get("sell") or 0))
        if etf and net:
            totals[etf] = totals.get(etf, 0) + net
    return [
        {"code": etf, "netShares": net, "netLots": round(net / 1000, 1)}
        for etf, net in sorted(totals.items(), key=lambda item: abs(item[1]), reverse=True)
    ]


def active_etf_flow_radar(
    trading_days: int = 5,
    end_date: str | None = None,
) -> dict[str, Any]:
    """回傳全市場主動式 ETF 異動、共同持有與權重排行。"""
    day_rows, last_error = _recent_trading_days(trading_days, end_date)
    if not day_rows:
        raise RuntimeError(last_error or "尚未取得主動式 ETF 持股異動")

    selected = day_rows[:trading_days]
    daily_maps: list[tuple[str, dict[str, int]]] = []
    names: dict[str, str] = {}
    all_etfs: set[str] = set()
    period_etfs: dict[str, set[str]] = {}
    latest_etfs: dict[str, set[str]] = {}
    five_day_etf_net: dict[str, dict[str, int]] = {}

    for day_index, (day, rows) in enumerate(selected):
        daily: dict[str, int] = {}
        for row in rows:
            component = str(row.get("component_stock_id") or "").strip().upper()
            etf = str(row.get("stock_id") or "").strip().upper()
            if not component or not _is_taiwan_stock_component(component, etf):
                continue
            name = str(row.get("component_stock_name") or "").strip()
            if name:
                names[component] = name
            net = int(float(row.get("buy") or 0)) - int(float(row.get("sell") or 0))
            daily[component] = daily.get(component, 0) + net
            if etf:
                all_etfs.add(etf)
            if etf and net:
                period_etfs.setdefault(component, set()).add(etf)
                if day_index == 0:
                    latest_etfs.setdefault(component, set()).add(etf)
                etf_totals = five_day_etf_net.setdefault(component, {})
                etf_totals[etf] = etf_totals.get(etf, 0) + net
        daily_maps.append((day, daily))

    holdings: list[dict[str, Any]] = []
    holdings_error: str | None = None
    holdings_date = selected[0][0]
    for day, _ in selected:
        try:
            holdings = _fetch_holdings_day(day)
        except Exception as exc:  # noqa: BLE001
            holdings_error = str(exc)
            continue
        if holdings:
            holdings_date = day
            break

    holding_by_stock: dict[str, list[dict[str, Any]]] = {}
    for row in holdings:
        if str(row.get("asset_type") or "").strip().lower() != "stock":
            continue
        component = str(row.get("component_stock_id") or "").strip().upper()
        etf = str(row.get("stock_id") or "").strip().upper()
        if not component or not etf or not _is_taiwan_stock_component(component, etf):
            continue
        name = str(row.get("component_stock_name") or "").strip()
        if name:
            names[component] = name
        all_etfs.add(etf)
        holding_by_stock.setdefault(component, []).append({
            "code": etf,
            "shares": int(float(row.get("shares") or 0)),
            "weight": round(float(row.get("weight") or 0), 4),
            "marketValue": round(float(row.get("market_value") or 0), 2),
            "currency": str(row.get("currency") or "TWD").strip().upper(),
        })

    tickers = set(holding_by_stock)
    for _, daily in daily_maps:
        tickers.update(daily)

    radar_rows: list[dict[str, Any]] = []
    for component in tickers:
        daily = [{"date": day, "netShares": values.get(component, 0)} for day, values in daily_maps]
        latest_net = daily[0]["netShares"] if daily else 0
        period_net = sum(item["netShares"] for item in daily)
        direction = 1 if latest_net > 0 else -1 if latest_net < 0 else 0
        consecutive_days = 0
        if direction:
            for item in daily:
                value = item["netShares"]
                if (direction > 0 and value <= 0) or (direction < 0 and value >= 0):
                    break
                consecutive_days += 1
        current_holdings = sorted(
            holding_by_stock.get(component, []),
            key=lambda item: item["weight"],
            reverse=True,
        )
        period_breakdown = [
            {"code": etf, "netShares": net, "netLots": round(net / 1000, 1)}
            for etf, net in sorted(
                five_day_etf_net.get(component, {}).items(),
                key=lambda item: abs(item[1]),
                reverse=True,
            )
            if net
        ]
        radar_rows.append({
            "ticker": component,
            "name": names.get(component, component),
            "latestNetShares": latest_net,
            "fiveDayNetShares": period_net,
            "latestNetLots": round(latest_net / 1000, 1),
            "fiveDayNetLots": round(period_net / 1000, 1),
            "latestEtfs": _etf_breakdown(selected[0][1], component),
            "fiveDayEtfs": period_breakdown,
            "latestActionEtfCount": len(latest_etfs.get(component, set())),
            "fiveDayActionEtfCount": len(period_etfs.get(component, set())),
            "holdingEtfCount": len(current_holdings),
            "totalWeight": round(sum(item["weight"] for item in current_holdings), 4),
            "averageWeight": round(
                sum(item["weight"] for item in current_holdings) / len(current_holdings), 4,
            ) if current_holdings else 0,
            "holdings": current_holdings,
            "consecutiveDirection": "buy" if direction > 0 else "sell" if direction < 0 else "flat",
            "consecutiveDays": consecutive_days,
            "daily": daily,
        })

    radar_rows.sort(key=lambda item: abs(item["latestNetShares"]), reverse=True)
    return {
        "ok": True,
        "dataDate": selected[0][0],
        "holdingsDate": holdings_date if holdings else None,
        "holdingsAvailable": bool(holdings),
        "holdingsError": holdings_error if not holdings else None,
        "tradingDays": len(selected),
        "availableDates": [day for day, _ in selected],
        "etfs": sorted(all_etfs),
        "etfCount": len(all_etfs),
        "rowCount": len(radar_rows),
        "rows": radar_rows,
        "source": "FinMind TaiwanStockActiveETFHolding + TaiwanStockActiveETFHoldingChange",
        "disclaimer": "持股異動包含申購贖回影響，不等同經理人主動買賣純額；金額欄位為以最新收盤價估算。",
    }
