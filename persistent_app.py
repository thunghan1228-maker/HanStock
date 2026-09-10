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
from daytrade_early_sell import early_sell_signal_snapshot, historical_early_sell_demo_snapshot
from daytrade_early_sell_collector import start_daytrade_early_sell_collector
from intraday_large_order_collector import (
    collector_status as intraday_large_order_status,
    start_intraday_large_order_collector,
)
from intraday_large_order import get_intraday_large_order_monitor, normalize_intraday_large_order_signal
from stock_history_service import get_stock_history_bars_5m
from stock_bar_bootstrap import stock_bar_repair_status
from stock_bar_repair_collector import start_stock_bar_repair_collector
from main_force_collector import start_main_force_collector
from main_force_store import load_main_force_bars, main_force_storage_status
from main_force_backfill_jobs import request_main_force_backfill
from broker_branch_weekly import (
    broker_branch_storage_status,
    normalize_daily_rows,
    read_latest_broker_branch_daily,
    read_latest_broker_branch_weekly,
    save_broker_branch_daily,
)
from finmind_broker_branch_collector import (
    collect_missing_latest_days,
    finmind_broker_collector_status,
    start_finmind_broker_branch_collector,
)
from finmind_active_etf_flow import active_etf_flow_for_ticker, active_etf_flow_radar
from daily_pick_collector import start_daily_pick_collector, stop_daily_pick_collector, daily_pick_collector_status


class GroupStrengthSnapshotBody(BaseModel):
    tradeDate: str = Field(min_length=10, max_length=10)
    bucketTs: int = Field(gt=0)
    ranks: dict[str, int]


class BrokerBranchDailyRow(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    tradeDate: str = Field(min_length=10, max_length=10)
    netAmount: float
    netLots: float | None = None
    concentration: float
    activeBranches: int = Field(ge=0)
    source: str = Field(default="official-broker-branch", min_length=1, max_length=80)


class BrokerBranchDailyBody(BaseModel):
    rows: list[BrokerBranchDailyRow]


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
    async with _market_data_lifespan(fastapi_app) as state:
        start_group_strength_collector()
        start_daytrade_early_sell_collector()
        start_intraday_large_order_collector()
        # 主力副圖是保留的核心功能。
        start_main_force_collector()
        start_stock_bar_repair_collector()
        start_finmind_broker_branch_collector()
        start_daily_pick_collector()
        try:
            yield state
        finally:
            stop_daily_pick_collector()


app.router.lifespan_context = _persistent_lifespan


@app.get("/api/hub/daily-picks/status")
def get_daily_pick_status() -> dict[str, Any]:
    return {"status": "ok", "data": daily_pick_collector_status()}


@app.get("/api/hub/persistence/status")
def get_persistence_status() -> dict[str, Any]:
    data = group_strength_storage_status()
    data["collectorEnabled"] = os.getenv(
        "HANSTOCK_GROUP_STRENGTH_COLLECTOR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    data["daytradeEarlySellCollectorEnabled"] = os.getenv(
        "HANSTOCK_EARLY_SELL_COLLECTOR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    data["intradayLargeOrder"] = intraday_large_order_status()
    data["mainForceCollectorEnabled"] = os.getenv(
        "HANSTOCK_MAIN_FORCE_COLLECTOR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    data["mainForceHistory"] = main_force_storage_status()
    data["brokerBranchWeekly"] = broker_branch_storage_status()
    data["finmindBrokerCollector"] = finmind_broker_collector_status()
    data["stockBarAutoRepairEnabled"] = os.getenv(
        "HANSTOCK_STOCK_BAR_REPAIR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    data["stockBarAutoRepair"] = stock_bar_repair_status()
    return data
