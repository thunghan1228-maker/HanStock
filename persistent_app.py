"""HanStock 持久化 app：只保留台股即時行情與主力副圖所需的持久化端點。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Query

from hanstock_app import app, _normalize_stock_code
from main_force_collector import start_main_force_collector
from main_force_store import load_main_force_bars, load_main_force_ranking, main_force_storage_status
from main_force_backfill_jobs import request_main_force_backfill
from otc_index import TW_TZ
from stock_history_service import get_stock_history_bars_5m
from stock_bar_bootstrap import stock_bar_repair_status


_market_data_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _persistent_lifespan(fastapi_app):
    async with _market_data_lifespan(fastapi_app) as state:
        # 主力副圖是唯一保留的持久化背景工作。
        # 備援 Railway 專案不登入 Shioaji，因此不啟動沒有工作的保存執行緒。
        from quote_service import quote_deployment_role

        if quote_deployment_role() == "primary":
            start_main_force_collector()
        try:
            yield state
        finally:
            pass


app.router.lifespan_context = _persistent_lifespan


@app.get("/api/hub/persistence/status")
def get_persistence_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "data": {
            "mainForceCollectorEnabled": os.getenv(
                "HANSTOCK_MAIN_FORCE_COLLECTOR_ENABLED", "true"
            ).strip().lower() not in {"0", "false", "no", "off"},
            "mainForceHistory": main_force_storage_status(),
            "stockBarAutoRepairEnabled": False,
            "stockBarAutoRepair": stock_bar_repair_status(),
        },
    }


@app.get("/api/hub/force/bars/{stock_code}")
def get_persisted_main_force_bars(
    stock_code: str,
    interval: str = Query("5m", pattern="^(1m|5m)$"),
    trade_date: str | None = Query(None),
    days: int = Query(31, ge=1, le=400),
    limit: int = Query(20000, ge=1, le=100000),
    backfill: bool = Query(True),
) -> dict[str, Any]:
    """讀取永久保存的主力進出副圖；可選擇建立受限歷史補抓請求。"""
    code = _normalize_stock_code(stock_code)
    date = trade_date
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    bars = load_main_force_bars(code, interval, trade_date=date, days=days, limit=limit)
    backfill_result = None
    if date and backfill:
        try:
            backfill_result = request_main_force_backfill(code, date)
        except ValueError as error:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "status": "ok",
        "code": code,
        "interval": interval,
        "tradeDate": date,
        "bar_count": len(bars),
        "bars": bars,
        "persistent": True,
        "source": "railway_sqlite_shioaji_ticks",
        "backfill": backfill_result,
    }


@app.get("/api/hub/main-force/ranking")
def get_main_force_ranking(
    interval: str = Query("5m", pattern="^(1m|5m)$"),
    trade_date: str | None = Query(None),
    limit: int = Query(30, ge=1, le=200),
) -> dict[str, Any]:
    """今日（或指定交易日）主力累計買賣超排行；只讀取既有主力副圖資料，不新增任何 Shioaji 連線。"""
    date = trade_date
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    else:
        date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    ranking = load_main_force_ranking(date, interval=interval, limit=limit)
    return {
        "status": "ok",
        "tradeDate": date,
        "interval": interval,
        "count": len(ranking),
        "ranking": ranking,
        "source": "railway_sqlite_shioaji_ticks",
    }


@app.get("/api/hub/history5m/{stock_code}")
def get_strategy_history_5m(
    stock_code: str,
    calendar_days: int = Query(14, ge=3, le=31),
) -> dict[str, Any]:
    """股票 K 線歷史資料；保留供即時行情頁面的個股圖表使用。"""
    code = _normalize_stock_code(stock_code)
    return get_stock_history_bars_5m(code, calendar_days=calendar_days)
