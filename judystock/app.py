"""Judy Stock API。

以 HanStock 的族群、Rule1、永豐即時行情、盤中大戶訊號（瞬間大單／主力
累積）為基礎，拿掉當沖隔日沖、券商分點（需要另一個 FinMind 付費帳號）、
三角收斂／VCP 盤後選股等模組後的版本。這個資料夾完全獨立部署，跟
HanStock 本身互不影響。
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from read_rule1_results import RESULT_PATH, load_rule1_results
from reconnect_monitor import get_reconnect_monitor
from stock_groups import STOCK_GROUPS, resolve_group_names
from group_strength_store import load_group_strength_history, save_group_strength_snapshot
from main_force_store import load_main_force_bars
from intraday_signal_store import (
    load_latest_signals,
    load_recent_trade_dates,
    load_signals_for_ticker,
    load_latest_signals_by_kind,
)
from intraday_large_order import (
    get_intraday_large_order_monitor,
    normalize_intraday_large_order_signal,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("judystock.api")

APP_VERSION = "2.0.0"

SHIOAJI_QUOTE_ENABLED = os.getenv("SHIOAJI_QUOTE_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    quote_svc = None
    monitor = get_reconnect_monitor()
    if SHIOAJI_QUOTE_ENABLED:
        logger.info("==== 啟動永豐即時行情 ====")
        try:
            from quote_service import get_quote_service
            from main_force_collector import start_main_force_collector
            from intraday_large_order_collector import start_intraday_large_order_collector

            quote_svc = get_quote_service()
            quote_svc.startup()
            monitor.start()
            start_main_force_collector()
            start_intraday_large_order_collector()
        except Exception as exc:
            logger.error("永豐即時行情啟動失敗（網站其他功能仍正常運作）：%s", exc)
    yield
    if quote_svc is not None:
        logger.info("==== 關閉永豐即時行情 ====")
        try:
            monitor.stop()
            quote_svc.shutdown()
        except Exception as exc:
            logger.warning("關閉即時行情時發生錯誤：%s", exc)


app = FastAPI(title="Judy Stock API", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# 共用小工具
# ------------------------------------------------------------------

def _normalize_stock_code(raw: str) -> str:
    code = str(raw).strip().upper()
    if not code or len(code) > 12 or not code.replace("-", "").isalnum():
        raise HTTPException(status_code=422, detail=f"股票代號格式不正確：{raw}")
    return code


def _split_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw.replace(" ", ",").split(","):
        code = item.strip().upper()
        if not code or code in seen:
            continue
        if len(code) > 12 or not code.replace("-", "").isalnum():
            raise HTTPException(status_code=422, detail=f"股票代號格式不正確：{code}")
        result.append(code)
        seen.add(code)
    return result


def _validate_trade_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    return value


def _expected_hub_secret() -> str:
    return os.getenv("JUDYSTOCK_HUB_KEY", "").strip() or os.getenv("JUDYSTOCK_SYNC_TOKEN", "").strip()


def _require_hub_auth(x_hub_key: str | None) -> None:
    expected = _expected_hub_secret()
    if not expected:
        raise HTTPException(status_code=503, detail="Hub 寫入金鑰尚未設定")
    supplied = (x_hub_key or "").strip()
    if not supplied or supplied != expected:
        raise HTTPException(status_code=401, detail="Hub key 驗證失敗")


def _quote_service_or_503():
    if not SHIOAJI_QUOTE_ENABLED:
        raise HTTPException(status_code=503, detail="即時行情服務未啟用。")
    from quote_service import get_quote_service

    svc = get_quote_service()
    if not svc.state.logged_in:
        raise HTTPException(status_code=503, detail="永豐尚未登入。")
    return svc


def _stock_payload(code: str, quote: dict[str, Any] | None) -> dict[str, Any]:
    return {"stock_code": code, "quote_available": quote is not None, "quote": quote}


def _sort_group_stocks(stocks: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "group_order":
        return stocks
    if sort == "code":
        return sorted(stocks, key=lambda item: item["stock_code"])
    return sorted(
        stocks,
        key=lambda item: (
            item["quote"] is not None,
            (item["quote"] or {}).get("pct_chg")
            if (item["quote"] or {}).get("pct_chg") is not None
            else float("-inf"),
        ),
        reverse=True,
    )


def _flatten_passed_stocks(results: dict[str, Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for group in results.get("groups", []):
        group_name = group.get("group_name", "")
        for stock in group.get("passed_stocks", []):
            flattened.append({"group_name": group_name, **stock})
    return flattened


def _latest_results_or_404() -> dict[str, Any]:
    try:
        return load_rule1_results()
    except RuntimeError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


class GroupStrengthSnapshotBody(BaseModel):
    tradeDate: str = Field(min_length=10, max_length=10)
    bucketTs: int = Field(gt=0)
    ranks: dict[str, int]


# ------------------------------------------------------------------
# 網站首頁
# ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    dashboard_path = Path(__file__).parent / "web" / "index.html"
    return dashboard_path.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# 健康檢查
# ------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    base: dict[str, Any] = {
        "api_status": "ok",
        "service": "Judy Stock API",
        "version": APP_VERSION,
        "rule1_result_exists": RESULT_PATH.exists(),
        "group_count": len(STOCK_GROUPS),
    }

    if SHIOAJI_QUOTE_ENABLED:
        try:
            from quote_service import get_quote_service

            svc = get_quote_service()
            base.update(svc.get_health())
            base["stock_realtime"] = svc.get_stock_health()
        except Exception as exc:
            logger.exception("無法取得行情服務狀態")
            base.update(
                {
                    "shioaji_logged_in": False,
                    "quote_connected": False,
                    "subscribed": False,
                    "quote_stale": True,
                    "data_source": "error",
                    "error_message": f"無法取得行情服務狀態：{exc}",
                    "stock_realtime": {"enabled": False},
                }
            )
    else:
        base.update(
            {
                "shioaji_logged_in": False,
                "quote_connected": False,
                "subscribed": False,
                "quote_stale": False,
                "data_source": "disabled",
                "error_message": None,
                "stock_realtime": {"enabled": False},
            }
        )
    return base


# ------------------------------------------------------------------
# 族群
# ------------------------------------------------------------------

@app.get("/api/groups")
def list_groups(include_stocks: bool = Query(default=False)) -> dict[str, Any]:
    groups = []
    for group_name, stocks in STOCK_GROUPS.items():
        item: dict[str, Any] = {"group_name": group_name, "stock_count": len(stocks)}
        if include_stocks:
            item["stocks"] = [{"stock_code": code, "stock_name": name} for code, name in stocks]
        groups.append(item)
    return {"group_count": len(groups), "groups": groups}


@app.get("/api/groups/{keyword}")
def get_groups_by_keyword(keyword: str) -> dict[str, Any]:
    try:
        group_names = resolve_group_names(keyword)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    groups = []
    for group_name in group_names:
        stocks = STOCK_GROUPS[group_name]
        groups.append(
            {
                "group_name": group_name,
                "stock_count": len(stocks),
                "stocks": [{"stock_code": code, "stock_name": name} for code, name in stocks],
            }
        )
    return {"keyword": keyword, "matched_groups": groups}


# ------------------------------------------------------------------
# Rule1
# ------------------------------------------------------------------

@app.get("/api/rule1/latest")
def latest_rule1(passed_only: bool = Query(default=False)) -> dict[str, Any]:
    results = _latest_results_or_404()
    if not passed_only:
        return results
    return {
        "strategy": results.get("strategy"),
        "generated_at": results.get("generated_at"),
        "summary": results.get("summary", {}),
        "passed_stocks": _flatten_passed_stocks(results),
    }


@app.get("/api/rule1/passed")
def passed_rule1() -> dict[str, Any]:
    results = _latest_results_or_404()
    stocks = _flatten_passed_stocks(results)
    return {
        "strategy": results.get("strategy"),
        "generated_at": results.get("generated_at"),
        "count": len(stocks),
        "stocks": stocks,
    }


@app.post("/api/rule1/sync", include_in_schema=False)
def sync_rule1(
    payload: dict[str, Any] = Body(...),
    x_sync_token: str | None = Header(default=None, alias="X-Judystock-Sync-Token"),
) -> dict[str, Any]:
    expected_token = os.getenv("JUDYSTOCK_SYNC_TOKEN", "")
    if not expected_token:
        raise HTTPException(status_code=503, detail="伺服器尚未設定同步金鑰。")
    if x_sync_token != expected_token:
        raise HTTPException(status_code=401, detail="同步金鑰不正確。")

    required_keys = {"generated_at", "summary", "groups"}
    if not required_keys.issubset(payload):
        raise HTTPException(status_code=422, detail="Rule1 JSON 格式不完整。")

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = RESULT_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(RESULT_PATH)

    return {"status": "ok", "message": "Rule1 結果同步成功", "generated_at": payload.get("generated_at")}


# ------------------------------------------------------------------
# 即時行情（永豐 Shioaji）
# ------------------------------------------------------------------

@app.get("/api/quote/futures")
def get_futures_quote() -> dict[str, Any]:
    svc = _quote_service_or_503()
    tick = svc.get_latest_tick()
    if tick is None:
        raise HTTPException(status_code=404, detail="尚未收到任何台指期行情資料。")
    return {"status": "ok", "data": tick}


@app.get("/api/realtime/latest")
def get_latest_stock_quotes(
    codes: str | None = Query(default=None, description="逗號分隔股票代號"),
    subscribe: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=190),
) -> dict[str, Any]:
    svc = _quote_service_or_503()
    requested_codes = _split_codes(codes)
    if not requested_codes:
        requested_codes = svc.get_active_stock_codes()[:limit]
    else:
        requested_codes = requested_codes[:limit]

    subscription = None
    if subscribe and requested_codes:
        subscription = svc.ensure_stock_subscriptions(requested_codes)

    quotes = svc.get_stock_quotes(requested_codes)
    data = [_stock_payload(code, quotes.get(code)) for code in requested_codes]
    return {
        "status": "ok",
        "requested_count": len(requested_codes),
        "available_count": sum(item["quote_available"] for item in data),
        "subscription": subscription,
        "data": data,
    }


@app.get("/api/realtime/group/{keyword}")
def get_group_realtime(
    keyword: str,
    subscribe: bool = Query(default=True),
    sort: str = Query(default="change_desc", pattern="^(change_desc|code|group_order)$"),
) -> dict[str, Any]:
    svc = _quote_service_or_503()
    try:
        group_names = resolve_group_names(keyword)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    unique_codes: list[str] = []
    seen: set[str] = set()
    for group_name in group_names:
        for code, _ in STOCK_GROUPS[group_name]:
            code = str(code).upper()
            if code not in seen:
                seen.add(code)
                unique_codes.append(code)

    max_codes = int(os.getenv("JUDYSTOCK_REALTIME_GROUP_MAX_CODES", "100"))
    max_codes = max(1, min(190, max_codes))
    limited_codes = unique_codes[:max_codes]
    truncated_codes = unique_codes[max_codes:]

    subscription = None
    if subscribe:
        subscription = svc.ensure_stock_subscriptions(limited_codes)

    quotes = svc.get_stock_quotes(limited_codes)
    groups: list[dict[str, Any]] = []
    for group_name in group_names:
        rows: list[dict[str, Any]] = []
        for code, name in STOCK_GROUPS[group_name]:
            code = str(code).upper()
            if code not in quotes:
                continue
            item = _stock_payload(code, quotes.get(code))
            item["stock_name"] = name
            rows.append(item)

        rows = _sort_group_stocks(rows, sort)
        for index, item in enumerate(rows, start=1):
            item["rank"] = index
        groups.append(
            {
                "group_name": group_name,
                "stock_count": len(STOCK_GROUPS[group_name]),
                "returned_count": len(rows),
                "available_quote_count": sum(row["quote_available"] for row in rows),
                "stocks": rows,
            }
        )

    return {
        "status": "ok",
        "keyword": keyword,
        "matched_group_count": len(groups),
        "requested_stock_count": len(limited_codes),
        "truncated_stock_codes": truncated_codes,
        "subscription": subscription,
        "groups": groups,
    }


# ------------------------------------------------------------------
# 主力累積（個股 1 分／5 分主力進出副圖）
# ------------------------------------------------------------------

@app.get("/api/hub/force/bars/{stock_code}")
def get_persisted_main_force_bars(
    stock_code: str,
    interval: str = Query("5m", pattern="^(1m|5m)$"),
    trade_date: str | None = Query(None),
    days: int = Query(31, ge=1, le=400),
    limit: int = Query(20000, ge=1, le=100000),
) -> dict[str, Any]:
    code = _normalize_stock_code(stock_code)
    date = _validate_trade_date(trade_date) if trade_date else None
    bars = load_main_force_bars(code, interval, trade_date=date, days=days, limit=limit)
    return {
        "status": "ok",
        "code": code,
        "interval": interval,
        "tradeDate": date,
        "bar_count": len(bars),
        "bars": bars,
    }


# ------------------------------------------------------------------
# 主力瞬間大單／特大買賣單
# ------------------------------------------------------------------

@app.get("/api/hub/intraday-large-orders")
def get_intraday_large_orders(limit: int = Query(100, ge=1, le=5000)) -> dict[str, Any]:
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
        (str(s.get("ticker") or ""), str(s.get("kind") or ""), int(s.get("barTs") or 0)): s
        for s in thresholded_signals
    }
    signals = list(signals_by_key.values())
    return {
        "status": "ok",
        "tradeDate": trade_date,
        "signals": sorted(signals, key=lambda row: int(row.get("barTs") or 0), reverse=True)[:limit],
    }


# ------------------------------------------------------------------
# 族群強弱歷史（大單偵測用，順便公開唯讀）
# ------------------------------------------------------------------

@app.get("/api/hub/group-strength/history")
def get_group_strength_history(trade_date: str = Query(..., min_length=10, max_length=10)) -> dict[str, Any]:
    date = _validate_trade_date(trade_date)
    snapshots = load_group_strength_history(date)
    return {"status": "ok", "tradeDate": date, "count": len(snapshots), "snapshots": snapshots}


@app.post("/api/hub/group-strength/history", include_in_schema=False)
def post_group_strength_history(
    body: GroupStrengthSnapshotBody,
    x_hub_key: str | None = Header(default=None, alias="X-Hub-Key"),
) -> dict[str, Any]:
    _require_hub_auth(x_hub_key)
    date = _validate_trade_date(body.tradeDate)
    if not body.ranks:
        raise HTTPException(status_code=422, detail="ranks 不可為空")
    count = save_group_strength_snapshot(date, body.bucketTs, body.ranks)
    return {"status": "ok", "tradeDate": date, "bucketTs": body.bucketTs, "snapshotCount": count}


# ------------------------------------------------------------------
# 今日即時（盤中訊號總覽）
# ------------------------------------------------------------------

@app.get("/api/hub/intraday-signals/latest")
def get_latest_intraday_signals(
    trade_date: str = Query(..., min_length=10, max_length=10),
    limit: int = Query(20, ge=1, le=200),
    market_only: bool = Query(False),
) -> dict[str, Any]:
    date = _validate_trade_date(trade_date)
    signals = load_latest_signals(date, limit=limit, market_only=market_only)
    return {"status": "ok", "tradeDate": date, "count": len(signals), "signals": signals}


@app.get("/api/hub/intraday-signals/ticker")
def get_intraday_signals_for_ticker(
    ticker: str = Query(..., min_length=1, max_length=16),
    trade_date: str | None = Query(None, min_length=10, max_length=10),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    date = _validate_trade_date(trade_date) if trade_date else None
    code = _normalize_stock_code(ticker)
    signals = load_signals_for_ticker(code, trade_date=date, limit=limit)
    return {"status": "ok", "ticker": code, "tradeDate": date, "count": len(signals), "signals": signals}


@app.get("/api/hub/intraday-signals/dates")
def get_intraday_signal_dates(limit: int = Query(10, ge=1, le=60)) -> dict[str, Any]:
    dates = load_recent_trade_dates(limit=limit)
    return {"status": "ok", "count": len(dates), "dates": dates}


# ------------------------------------------------------------------
# 四項精選（讀取另一個「Battle」網站已經算好的每日多空清單）
# ------------------------------------------------------------------

_battle_cache: dict[str, Any] = {"payload": None, "fetched_at": 0.0}
BATTLE_CACHE_SECONDS = 60
BATTLE_SITE_URL = os.getenv(
    "JUDYSTOCK_BATTLE_SITE_URL",
    os.getenv("HANSTOCK_BATTLE_SITE_URL", "https://hanstock-battle-minimal.thunghan8.chatgpt.site"),
).rstrip("/")


@app.get("/api/battle/daily-picks")
def get_battle_daily_picks() -> dict[str, Any]:
    now = time.monotonic()
    cached = _battle_cache["payload"]
    if cached is not None and now - _battle_cache["fetched_at"] < BATTLE_CACHE_SECONDS:
        payload = cached
    else:
        url = f"{BATTLE_SITE_URL}/api/daily-pick-list"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "JudyStock-DailyPicks/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"四項精選暫時無法取得：{exc}") from exc
        _battle_cache["payload"] = payload
        _battle_cache["fetched_at"] = now

    snapshot = payload.get("snapshot") or {}
    return {
        "status": "ok",
        "tradeDate": snapshot.get("tradeDate"),
        "computedAt": snapshot.get("computedAt"),
        "bull": snapshot.get("bull", []),
        "bear": snapshot.get("bear", []),
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
