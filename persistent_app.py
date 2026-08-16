"""HanStock 正式 app：在既有行情 app 上增加 Railway SQLite 持久化端點。"""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Header, HTTPException, Query
from pydantic import BaseModel, Field

from group_strength_collector import start_group_strength_collector
from group_strength_store import (
    group_strength_storage_status,
    load_group_strength_history,
    save_group_strength_snapshot,
)
from hanstock_app import _normalize_stock_code, app
from intraday_signal_collector import start_intraday_signal_collector
from daytrade_flow_collector import start_daytrade_flow_collector
from daytrade_early_sell import early_sell_signal_snapshot
from daytrade_early_sell_collector import collect_once as collect_early_sell_once
from daytrade_early_sell_collector import start_daytrade_early_sell_collector
from daytrade_flow_store import load_daytrade_scan_status
from intraday_signal_store import (
    intraday_signal_count,
    load_latest_signals,
    load_recent_trade_dates,
    load_signals_for_ticker,
)
from stock_history_service import get_stock_history_bars_5m


class GroupStrengthSnapshotBody(BaseModel):
    tradeDate: str = Field(min_length=10, max_length=10)
    bucketTs: int = Field(gt=0)
    ranks: dict[str, int]


def _validate_trade_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    return value


def _expected_hub_secret() -> str:
    return (
        os.getenv("HANSTOCK_HUB_KEY", "").strip()
        or os.getenv("HANSTOCK_SYNC_TOKEN", "").strip()
    )


def _require_hub_auth(x_hub_key: str | None) -> None:
    expected = _expected_hub_secret()
    if not expected:
        raise HTTPException(status_code=503, detail="Hub 持久化寫入金鑰尚未設定")
    supplied = (x_hub_key or "").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Hub key 驗證失敗")


_market_data_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _persistent_lifespan(fastapi_app):
    # api_server 已設定 lifespan；FastAPI 不會再執行同一個 app 上的舊式
    # @app.on_event("startup")。把持久化工作明確包進原 lifespan，Railway
    # 啟動時才會真的開啟兩個背景收集器。
    async with _market_data_lifespan(fastapi_app) as state:
        # 兩個背景工作都由 Hub 自己拉「公開/唯讀計算結果」後寫入自己的
        # /data SQLite；Vercel 不需要持有 Railway 寫入金鑰。
        start_group_strength_collector()
        start_intraday_signal_collector()
        start_daytrade_flow_collector()
        start_daytrade_early_sell_collector()
        yield state


app.router.lifespan_context = _persistent_lifespan


@app.get("/api/hub/persistence/status")
def get_persistence_status() -> dict[str, Any]:
    data = group_strength_storage_status()
    data["collectorEnabled"] = os.getenv(
        "HANSTOCK_GROUP_STRENGTH_COLLECTOR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    data["intradaySignalCollectorEnabled"] = os.getenv(
        "HANSTOCK_INTRADAY_SIGNAL_COLLECTOR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    data["intradaySignalCount"] = intraday_signal_count()
    data["daytradeFlowCollectorEnabled"] = os.getenv(
        "HANSTOCK_DAYTRADE_COLLECTOR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    data["daytradeFlowLatestScan"] = load_daytrade_scan_status()
    data["daytradeEarlySellCollectorEnabled"] = os.getenv(
        "HANSTOCK_EARLY_SELL_COLLECTOR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    return {"status": "ok", "data": data}


@app.get("/api/hub/daytrade-early-sell-signals")
def get_daytrade_early_sell_signals(
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """09:00～09:30（含）累計大單賣出達前日預估隔日賣壓 50% 的即時訊號。"""
    # API 被讀取時順手補跑一次；背景執行緒仍是主要來源，因此頁面未開啟也會監控。
    try:
        runtime = collect_early_sell_once()
    except Exception:  # noqa: BLE001
        runtime = {"prepared": False, "inserted": []}
    snapshot = early_sell_signal_snapshot(limit=limit)
    return {
        "status": "ok",
        **snapshot,
        "prepared": bool(runtime.get("prepared")),
        "activeCount": int(runtime.get("activeCount") or 0),
        "failedCount": int(runtime.get("failedCount") or 0),
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@app.get("/api/hub/history5m/{stock_code}")
def get_strategy_history_5m(
    stock_code: str,
    calendar_days: int = Query(14, ge=3, le=31),
) -> dict[str, Any]:
    """盤中策略專用多日 5 分 K：Shioaji kbars 歷史 + 今日 Hub，即時/歷史同源。"""
    code = _normalize_stock_code(stock_code)
    return get_stock_history_bars_5m(code, calendar_days=calendar_days)


@app.get("/api/hub/group-strength/history")
def get_group_strength_history(
    trade_date: str = Query(..., min_length=10, max_length=10),
) -> dict[str, Any]:
    # 排名歷史本身就是公開網站資料；只開放唯讀 GET，寫入仍需金鑰。
    date = _validate_trade_date(trade_date)
    snapshots = load_group_strength_history(date)
    return {
        "status": "ok",
        "tradeDate": date,
        "count": len(snapshots),
        "snapshots": snapshots,
    }


@app.post("/api/hub/group-strength/history")
def post_group_strength_history(
    body: GroupStrengthSnapshotBody,
    x_hub_key: str | None = Header(default=None, alias="X-Hub-Key"),
) -> dict[str, Any]:
    _require_hub_auth(x_hub_key)
    date = _validate_trade_date(body.tradeDate)
    if not body.ranks:
        raise HTTPException(status_code=422, detail="ranks 不可為空")
    if len(body.ranks) > 200:
        raise HTTPException(status_code=422, detail="單次最多 200 個族群名次")
    count = save_group_strength_snapshot(date, body.bucketTs, body.ranks)
    return {
        "status": "ok",
        "tradeDate": date,
        "bucketTs": body.bucketTs,
        "saved": len(body.ranks),
        "snapshotCount": count,
    }


@app.get("/api/hub/intraday-signals/latest")
def get_latest_intraday_signals(
    trade_date: str = Query(..., min_length=10, max_length=10),
    limit: int = Query(20, ge=1, le=200),
    market_only: bool = Query(False),
) -> dict[str, Any]:
    date = _validate_trade_date(trade_date)
    signals = load_latest_signals(date, limit=limit, market_only=market_only)
    return {
        "status": "ok",
        "tradeDate": date,
        "marketOnly": bool(market_only),
        "count": len(signals),
        "signals": signals,
    }


@app.get("/api/hub/intraday-signals/ticker")
def get_intraday_signals_for_ticker(
    ticker: str = Query(..., min_length=1, max_length=16),
    trade_date: str | None = Query(None, min_length=10, max_length=10),
    since_ts: int | None = Query(None, ge=1),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    date = _validate_trade_date(trade_date) if trade_date else None
    code = ticker.strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="ticker 不可為空")
    signals = load_signals_for_ticker(code, trade_date=date, since_ts=since_ts, limit=limit)
    return {
        "status": "ok",
        "ticker": code,
        "tradeDate": date,
        "sinceTs": since_ts,
        "count": len(signals),
        "signals": signals,
    }


@app.get("/api/hub/intraday-signals/dates")
def get_intraday_signal_dates(
    limit: int = Query(10, ge=1, le=60),
) -> dict[str, Any]:
    dates = load_recent_trade_dates(limit=limit)
    return {"status": "ok", "count": len(dates), "dates": dates}
