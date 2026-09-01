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
from daytrade_early_sell import early_sell_signal_snapshot, historical_early_sell_demo_snapshot
from daytrade_early_sell_collector import start_daytrade_early_sell_collector
from intraday_large_order_collector import (
    collector_status as intraday_large_order_status,
    start_intraday_large_order_collector,
)
from intraday_large_order import get_intraday_large_order_monitor, normalize_intraday_large_order_signal
from daytrade_flow_store import load_daytrade_scan_status
from intraday_signal_store import (
    intraday_signal_count,
    load_latest_signals,
    load_latest_signals_by_kind,
    load_recent_trade_dates,
    load_signals_for_ticker,
)
from stock_history_service import get_stock_history_bars_5m
from stock_bar_bootstrap import stock_bar_repair_status
from stock_bar_repair_collector import start_stock_bar_repair_collector
from triangle_intraday import intraday_triangle_status
from triangle_intraday_collector import start_triangle_intraday_collector
from triangle_daily_collector import (
    start_triangle_daily_collector,
    triangle_daily_collector_status,
)
from main_force_collector import start_main_force_collector
from main_force_store import load_main_force_bars, main_force_storage_status
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
        start_intraday_large_order_collector()
        start_main_force_collector()
        start_stock_bar_repair_collector()
        start_triangle_intraday_collector()
        start_triangle_daily_collector()
        start_finmind_broker_branch_collector()
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
    data["triangleIntradayCollectorEnabled"] = os.getenv(
        "HANSTOCK_TRIANGLE_INTRADAY_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    data["triangleIntraday"] = intraday_triangle_status()
    data["triangleDailyCollectorEnabled"] = os.getenv(
        "HANSTOCK_TRIANGLE_DAILY_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    data["triangleDaily"] = triangle_daily_collector_status()
    return {"status": "ok", "data": data}


@app.post("/api/hub/broker-branch-daily")
def post_broker_branch_daily(
    body: BrokerBranchDailyBody,
    x_hub_key: str | None = Header(default=None, alias="X-Hub-Key"),
) -> dict[str, Any]:
    """接收合法資料供應端彙整后的每日券商分點資料；公開瀏覽器不可寫入。"""
    _require_hub_auth(x_hub_key)
    if not body.rows:
        raise HTTPException(status_code=422, detail="rows 不可為空")
    if len(body.rows) > 5000:
        raise HTTPException(status_code=413, detail="單次最多同步 5000 筆分點資料")
    try:
        rows = normalize_daily_rows(row.model_dump() for row in body.rows)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    saved = save_broker_branch_daily(rows)
    return {
        "status": "ok",
        "saved": saved,
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@app.get("/api/hub/broker-branch-weekly")
def get_broker_branch_weekly() -> dict[str, Any]:
    """公开只读：最近五个交易日的券商分点周净额与集中度。"""
    result = read_latest_broker_branch_weekly()
    return {
        "ok": bool(result["rows"]),
        **result,
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "railway-sqlite-official-broker-branch",
    }


@app.get("/api/hub/broker-branch-daily")
def get_broker_branch_daily() -> dict[str, Any]:
    """公開唯讀：最新交易日的券商分點淨額、集中度與分點數。"""
    result = read_latest_broker_branch_daily()
    return {
        "ok": bool(result["rows"]),
        **result,
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "railway-sqlite-official-broker-branch-daily",
    }


@app.get("/api/hub/active-etf-flow")
def get_active_etf_flow(
    ticker: str = Query(..., min_length=1, max_length=16),
    days: int = Query(5, ge=1, le=10),
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, Any]:
    """公開唯讀：FinMind 主動式 ETF 對指定成份股的每日與五日持股異動。"""
    try:
        return active_etf_flow_for_ticker(
            _normalize_stock_code(ticker),
            trading_days=days,
            end_date=date,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/hub/active-etf-radar")
def get_active_etf_radar(
    days: int = Query(5, ge=1, le=10),
    date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, Any]:
    """公開唯讀：全市場主動式 ETF 異動、共同持有與權重排行。"""
    try:
        return active_etf_flow_radar(trading_days=days, end_date=date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/hub/broker-branch-finmind/backfill")
def backfill_finmind_broker_branch(
    x_hub_key: str | None = Header(default=None, alias="X-Hub-Key"),
) -> dict[str, Any]:
    """受保護的手動補抓；一般情況由 Railway 背景排程自動執行。"""
    _require_hub_auth(x_hub_key)
    return {"status": "ok", "data": collect_missing_latest_days(5)}


@app.get("/api/hub/force/bars/{stock_code}")
def get_persisted_main_force_bars(
    stock_code: str,
    interval: str = Query("5m", pattern="^(1m|5m)$"),
    trade_date: str | None = Query(None),
    days: int = Query(31, ge=1, le=400),
    limit: int = Query(20000, ge=1, le=100000),
    backfill: bool = Query(True),
) -> dict[str, Any]:
    """跨日讀取永久保存的主力進出副圖；只回傳真實逐筆統計。"""
    code = _normalize_stock_code(stock_code)
    date = _validate_trade_date(trade_date) if trade_date else None
    bars = load_main_force_bars(code, interval, trade_date=date, days=days, limit=limit)
    backfill_result = None
    if date and not bars and backfill:
        from stock_bar_bootstrap import backfill_main_force_date
        backfill_result = backfill_main_force_date(code, date)
        bars = load_main_force_bars(code, interval, trade_date=date, days=days, limit=limit)
    return {
        "status": "ok", "code": code, "interval": interval,
        "tradeDate": date, "bar_count": len(bars), "bars": bars,
        "persistent": True, "source": "railway_sqlite_shioaji_ticks",
        "backfill": backfill_result,
    }


@app.get("/api/hub/daytrade-early-sell-signals")
def get_daytrade_early_sell_signals(
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """09:00～13:30（含）逐分鐘累計大單買進／賣出達前日預估賣壓 50% 的訊號。"""
    # 背景收集器已每 5 秒計算並永久保存；公開 GET 只讀快照。
    # 不可在每台裝置的輪詢請求內重跑全候選股，否則同時讀取會塞滿
    # FastAPI 執行緒池，連健康檢查與後續訊號都會一起停住。
    snapshot = early_sell_signal_snapshot(limit=limit)
    from triangle_intraday import SIGNAL_KIND_BY_STATUS

    triangle_signals = [
        signal
        for kind in SIGNAL_KIND_BY_STATUS.values()
        for signal in load_latest_signals_by_kind(snapshot["tradeDate"], kind, limit=limit)
    ]
    instant_large_signals = [
        normalized
        for kind in ("instantLargeBuy", "instantLargeSell")
        for signal in load_latest_signals_by_kind(snapshot["tradeDate"], kind, limit=limit)
        if (normalized := normalize_intraday_large_order_signal(signal)) is not None
    ]
    snapshot["signals"] = sorted(
        [*snapshot.get("signals", []), *triangle_signals, *instant_large_signals],
        key=lambda signal: (int(signal.get("barTs") or 0), str(signal.get("ticker") or "")),
        reverse=True,
    )[:limit]
    return {
        "status": "ok",
        **snapshot,
        "prepared": bool(snapshot.get("monitoredCount")),
        "activeCount": int(snapshot.get("monitoredCount") or 0),
        "failedCount": 0,
        "collectorMode": "background_snapshot_only",
        "excludedTickers": snapshot.get("excludedTickers") or [],
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@app.get("/api/hub/intraday-large-orders")
def get_intraday_large_orders(limit: int = Query(100, ge=1, le=5000)) -> dict[str, Any]:
    """讀回當日完整族群瞬間大單；上限涵蓋整個交易時段而非最後 500 筆。"""
    trade_date = datetime.now().astimezone().strftime("%Y-%m-%d")
    stored_signals = [
        signal
        for kind in ("instantLargeBuy", "instantLargeSell")
        for signal in load_latest_signals_by_kind(trade_date, kind, limit=limit)
    ]
    memory_signals = get_intraday_large_order_monitor().recent_signals(trade_date, limit=limit)
    thresholded_signals = [
        normalized
        for signal in [*stored_signals, *memory_signals]
        if (normalized := normalize_intraday_large_order_signal(signal)) is not None
    ]
    signals_by_key = {
        (str(signal.get("ticker") or ""), str(signal.get("kind") or ""), int(signal.get("barTs") or 0)): signal
        for signal in thresholded_signals
    }
    signals = list(signals_by_key.values())
    return {
        "status": "ok",
        "tradeDate": trade_date,
        "signals": sorted(signals, key=lambda row: int(row.get("barTs") or 0), reverse=True)[:limit],
        "collector": intraday_large_order_status(),
    }


@app.get("/api/hub/daytrade-early-sell-demo")
def get_daytrade_early_sell_demo(
    date: str = Query(..., description="歷史交易日 YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """以歷史逐筆成交重播盤中警示；不寫入正式訊號資料庫。"""
    trade_date = _validate_trade_date(date)
    from quote_service import get_quote_service

    try:
        snapshot = historical_early_sell_demo_snapshot(
            get_quote_service(), trade_date, limit=limit
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "ok",
        **snapshot,
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
