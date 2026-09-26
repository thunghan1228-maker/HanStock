"""HanStock 精簡版 API。

只保留：台股即時行情、股票 1m/5m Hub、族群查詢與 WebSocket。
Rule1、三角收斂、VCP、台指期、OTC 指數與疑似隔日沖資金流已移除。
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from config import SHIOAJI_QUOTE_ENABLED
from market_data_hub import get_market_data_hub
from reconnect_monitor import get_reconnect_monitor
from stock_groups import STOCK_GROUPS, resolve_group_names
from ws_server import websocket_endpoint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hanstock.api")

API_VERSION = "2.0.0-lean"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
TW_TZ = timezone(timedelta(hours=8))


@asynccontextmanager
async def lifespan(app: FastAPI):
    quote_svc = None
    quote_startup_thread = None
    monitor = get_reconnect_monitor()

    if SHIOAJI_QUOTE_ENABLED:
        try:
            from quote_service import get_quote_service, quote_startup_delay_seconds

            quote_svc = get_quote_service()
            startup_delay = quote_startup_delay_seconds()
            if startup_delay > 0:
                def delayed_startup() -> None:
                    if quote_svc._shutdown_event.wait(startup_delay):
                        return
                    try:
                        quote_svc.startup()
                        monitor.start()
                        logger.info("Market Data Hub 延遲啟動完成")
                    except Exception as exc:
                        logger.error("Shioaji 延遲啟動失敗: %s", exc)

                quote_startup_thread = threading.Thread(
                    target=delayed_startup,
                    name="railway-delayed-shioaji-startup",
                    daemon=True,
                )
                quote_startup_thread.start()
                logger.info("API 先上線，Shioaji 延遲 %.0f 秒登入。", startup_delay)
            else:
                quote_svc.startup()
                monitor.start()
                logger.info("Market Data Hub 已啟動")
        except Exception as exc:
            logger.error("Shioaji 即時行情啟動失敗（API 仍繼續運作）: %s", exc)
    else:
        logger.info("SHIOAJI_QUOTE_ENABLED=false，跳過即時行情啟動。")

    yield

    if quote_svc is not None:
        try:
            monitor.stop()
            quote_svc.shutdown()
            if quote_startup_thread and quote_startup_thread.is_alive():
                quote_startup_thread.join(timeout=5)
        except Exception as exc:
            logger.warning("Shioaji 關閉時發生錯誤: %s", exc)


def _allowed_origins() -> list[str]:
    raw = os.getenv(
        "HANSTOCK_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


@lru_cache(maxsize=1)
def _stock_name_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for stocks in STOCK_GROUPS.values():
        for code, name in stocks:
            mapping.setdefault(str(code).upper(), name)
    return mapping


def _quote_service_or_503():
    if not SHIOAJI_QUOTE_ENABLED:
        raise HTTPException(status_code=503, detail="即時行情服務未啟用。")
    from quote_service import get_quote_service

    svc = get_quote_service()
    if not svc.state.logged_in:
        raise HTTPException(status_code=503, detail="Shioaji 尚未登入。")
    return svc


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


def _stock_payload(code: str, quote: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "stock_code": code,
        "stock_name": _stock_name_map().get(code),
        "quote_available": quote is not None,
        "quote": quote,
    }


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


app = FastAPI(
    title="HanStock API",
    description="HanStock 台灣股票即時行情與主力副圖 API",
    version=API_VERSION,
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def website_redirect() -> RedirectResponse:
    return RedirectResponse(url="/hub-dashboard", status_code=307)


@app.get("/hub-dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    dashboard_path = Path(__file__).parent / "web" / "index.html"
    if not dashboard_path.exists():
        return "<h1>HanStock API</h1><p>找不到 web/index.html。</p>"
    return dashboard_path.read_text(encoding="utf-8")


@app.get("/realtime-demo", response_class=HTMLResponse, include_in_schema=False)
def realtime_demo() -> str:
    demo_path = Path(__file__).parent / "web" / "realtime-demo.html"
    if not demo_path.exists():
        raise HTTPException(status_code=404, detail="找不到即時行情測試頁。")
    return demo_path.read_text(encoding="utf-8")


@app.get("/assets/realtime-radar-client.js", include_in_schema=False)
def realtime_client_asset() -> FileResponse:
    asset_path = Path(__file__).parent / "web" / "realtime-radar-client.js"
    if not asset_path.exists():
        raise HTTPException(status_code=404, detail="找不到即時行情前端連接器。")
    return FileResponse(asset_path, media_type="text/javascript; charset=utf-8")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")
    base: dict[str, Any] = {
        "api_status": "ok",
        "service": "HanStock API",
        "version": API_VERSION,
        "server_time": now,
        "group_count": len(STOCK_GROUPS),
        "deployment": {
            "provider": "railway" if os.getenv("RAILWAY_PROJECT_ID") else "local",
            "project_id": os.getenv("RAILWAY_PROJECT_ID"),
            "service_id": os.getenv("RAILWAY_SERVICE_ID"),
            "environment_id": os.getenv("RAILWAY_ENVIRONMENT_ID"),
            "quote_role": (
                "primary"
                if not os.getenv("RAILWAY_PROJECT_ID")
                or os.getenv("RAILWAY_PROJECT_ID")
                == os.getenv(
                    "HANSTOCK_PRIMARY_RAILWAY_PROJECT_ID",
                    "4b2403bb-cd2d-4917-bd8f-80dffe894d00",
                )
                else "standby"
            ),
        },
    }

    if SHIOAJI_QUOTE_ENABLED:
        try:
            from quote_service import get_quote_service

            svc = get_quote_service()
            base.update(svc.get_health())
            base["stock_realtime"] = svc.get_stock_health()
        except Exception as exc:
            base.update({
                "shioaji_initialized": False,
                "shioaji_logged_in": False,
                "certificate_active": False,
                "quote_connected": False,
                "subscribed": False,
                "last_quote_time": None,
                "quote_age_seconds": None,
                "quote_stale": True,
                "current_contract": None,
                "last_event": None,
                "data_source": "error",
                "reconnect_count": 0,
                "error_message": f"無法取得行情服務狀態: {exc}",
                "stock_realtime": {"enabled": False},
            })
    else:
        base.update({
            "shioaji_initialized": False,
            "shioaji_logged_in": False,
            "certificate_active": False,
            "quote_connected": False,
            "subscribed": False,
            "last_quote_time": None,
            "quote_age_seconds": None,
            "quote_stale": False,
            "current_contract": None,
            "last_event": None,
            "data_source": "disabled",
            "reconnect_count": 0,
            "error_message": None,
            "stock_realtime": {"enabled": False},
        })
    return base


@app.get("/api/realtime/status")
def get_realtime_status() -> dict[str, Any]:
    svc = _quote_service_or_503()
    return {"status": "ok", "data": svc.get_stock_health()}


@app.get("/api/realtime/latest")
def get_latest_stock_quotes(
    codes: str | None = Query(default=None, description="逗號分隔股票代號"),
    subscribe: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=190),
) -> dict[str, Any]:
    svc = _quote_service_or_503()
    requested_codes = _split_codes(codes)
    requested_codes = (requested_codes or svc.get_active_stock_codes())[:limit]
    subscription = svc.ensure_stock_subscriptions(requested_codes) if subscribe and requested_codes else None
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

    max_codes = max(1, min(190, int(os.getenv("HANSTOCK_REALTIME_GROUP_MAX_CODES", "100"))))
    limited_codes = unique_codes[:max_codes]
    truncated_codes = unique_codes[max_codes:]
    subscription = svc.ensure_stock_subscriptions(limited_codes) if subscribe else None
    quotes = svc.get_stock_quotes(limited_codes)

    groups: list[dict[str, Any]] = []
    for group_name in group_names:
        rows: list[dict[str, Any]] = []
        for code, name in STOCK_GROUPS[group_name]:
            code = str(code).upper()
            if code in quotes:
                item = _stock_payload(code, quotes.get(code))
                item["stock_name"] = name
                rows.append(item)
        rows = _sort_group_stocks(rows, sort)
        for index, item in enumerate(rows, start=1):
            item["rank"] = index
        groups.append({
            "group_name": group_name,
            "stock_count": len(STOCK_GROUPS[group_name]),
            "returned_count": len(rows),
            "available_quote_count": sum(row["quote_available"] for row in rows),
            "stocks": rows,
        })

    return {
        "status": "ok",
        "keyword": keyword,
        "matched_group_count": len(groups),
        "requested_stock_count": len(limited_codes),
        "truncated_stock_codes": truncated_codes,
        "subscription": subscription,
        "groups": groups,
    }


@app.get("/api/realtime/{stock_code}")
def get_stock_realtime(stock_code: str, subscribe: bool = Query(default=True)) -> dict[str, Any]:
    svc = _quote_service_or_503()
    code = _normalize_stock_code(stock_code)
    subscription = svc.ensure_stock_subscriptions([code]) if subscribe else None
    quote = svc.get_stock_quote(code)
    return {
        "status": "ok" if quote else "waiting",
        "subscription": subscription,
        "data": _stock_payload(code, quote),
    }


@app.get("/api/groups")
def list_groups(include_stocks: bool = Query(default=False)) -> dict[str, Any]:
    groups = []
    for group_name, stocks in STOCK_GROUPS.items():
        item: dict[str, Any] = {"group_name": group_name, "stock_count": len(stocks)}
        if include_stocks:
            item["stocks"] = [
                {"stock_code": code, "stock_name": name} for code, name in stocks
            ]
        groups.append(item)
    return {"group_count": len(groups), "groups": groups}


@app.get("/api/groups/{keyword}")
def get_groups_by_keyword(keyword: str) -> dict[str, Any]:
    try:
        group_names = resolve_group_names(keyword)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "keyword": keyword,
        "matched_groups": [
            {
                "group_name": name,
                "stock_count": len(STOCK_GROUPS[name]),
                "stocks": [
                    {"stock_code": code, "stock_name": stock_name}
                    for code, stock_name in STOCK_GROUPS[name]
                ],
            }
            for name in group_names
        ],
    }


# ------------------------------------------------------------------
# Market Data Hub：只保留股票即時 1m/5m 資料。
# ------------------------------------------------------------------

@app.get("/api/hub/status")
def get_hub_status() -> dict[str, Any]:
    hub = get_market_data_hub()
    monitor = get_reconnect_monitor()
    return {"status": "ok", "data": hub.get_hub_status(), "reconnect": monitor.get_status()}


@app.get("/api/hub/ticks")
async def get_hub_ticks(codes: str | None = Query(default=None)) -> dict[str, Any]:
    hub = get_market_data_hub()
    code_list = _split_codes(codes)
    ticks = hub.get_ticks(code_list) if code_list else hub.get_all_ticks()
    return {"status": "ok", "count": len(ticks), "data": ticks}


@app.get("/api/hub/bars1m/{stock_code}")
def get_hub_bars_1m(stock_code: str) -> dict[str, Any]:
    hub = get_market_data_hub()
    code = _normalize_stock_code(stock_code)
    bars = hub.get_live_bars_1m(code)
    return {"status": "ok", "code": code, "interval": "1m", "bar_count": len(bars), "bars": bars}


@app.post("/api/hub/bars1m/batch")
async def get_hub_bars_1m_batch(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    raw_codes = payload.get("codes", [])
    if not isinstance(raw_codes, list):
        raise HTTPException(status_code=422, detail="codes 必須為陣列")
    codes: list[str] = []
    seen: set[str] = set()
    for raw_code in raw_codes[:200]:
        code = _normalize_stock_code(str(raw_code))
        if code not in seen:
            codes.append(code)
            seen.add(code)
    result = get_market_data_hub().get_live_bars_1m_batch(codes)
    return {"status": "ok", "interval": "1m", "requested_count": len(codes), "data": result}


@app.get("/api/hub/bars/{stock_code}")
def get_hub_bars(stock_code: str) -> dict[str, Any]:
    hub = get_market_data_hub()
    code = _normalize_stock_code(stock_code)
    bars = hub.get_live_bars(code)
    return {"status": "ok", "code": code, "interval": "5m", "bar_count": len(bars), "bars": bars}


@app.post("/api/hub/bars/batch")
async def get_hub_bars_batch(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    raw_codes = payload.get("codes", [])
    if not isinstance(raw_codes, list):
        raise HTTPException(status_code=422, detail="codes 必須為陣列")
    codes = [_normalize_stock_code(str(code)) for code in raw_codes[:200] if code]
    result = get_market_data_hub().get_live_bars_batch(codes)
    return {"status": "ok", "requested_count": len(codes), "data": result}


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    await websocket_endpoint(websocket)
