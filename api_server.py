"""HanStock 網站 API。

提供族群、Rule1、台指期及台股動態即時行情，供 hanstock.xyz、
台股族群雷達、LINE Bot 或 App 使用。

v1.2.0: 新增台股動態訂閱與族群即時行情 API。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from config import SHIOAJI_QUOTE_ENABLED
from read_rule1_results import RESULT_PATH, load_rule1_results
from stock_groups import STOCK_GROUPS, resolve_group_names

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hanstock.api")

API_VERSION = "1.2.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
TW_TZ = timezone(timedelta(hours=8))


@asynccontextmanager
async def lifespan(app: FastAPI):
    quote_svc = None
    if SHIOAJI_QUOTE_ENABLED:
        logger.info("＝＝＝＝ 啟動 Shioaji 即時行情服務 ＝＝＝＝")
        try:
            from quote_service import get_quote_service

            quote_svc = get_quote_service()
            quote_svc.startup()
        except Exception as exc:
            logger.error("Shioaji 即時行情啟動失敗（API 仍繼續運作）: %s", exc)
    else:
        logger.info("SHIOAJI_QUOTE_ENABLED=false，跳過即時行情啟動。")

    yield

    if quote_svc is not None:
        logger.info("＝＝＝＝ 關閉 Shioaji 即時行情服務 ＝＝＝＝")
        try:
            quote_svc.shutdown()
        except Exception as exc:
            logger.warning("Shioaji 關閉時發生錯誤: %s", exc)


def _allowed_origins() -> list[str]:
    raw = os.getenv(
        "HANSTOCK_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,https://hanstock.xyz,https://www.hanstock.xyz",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


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


def _validate_sync_payload(payload: dict[str, Any]) -> None:
    required_keys = {"generated_at", "summary", "groups"}
    if not required_keys.issubset(payload):
        raise HTTPException(status_code=422, detail="Rule1 JSON 格式不完整。")


def _save_synced_results(payload: dict[str, Any]) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = RESULT_PATH.with_suffix(".tmp")
    import json

    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary_path.replace(RESULT_PATH)


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

    # 預設依漲跌幅由高到低；無行情排在最後。
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
    description="HanStock 台灣股票族群、Rule1、台指期及台股即時行情 API",
    version=API_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
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
def health() -> dict[str, Any]:
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")
    base: dict[str, Any] = {
        "api_status": "ok",
        "service": "HanStock API",
        "version": API_VERSION,
        "server_time": now,
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


@app.get("/api/quote/futures")
def get_futures_quote() -> dict[str, Any]:
    svc = _quote_service_or_503()
    tick = svc.get_latest_tick()
    if tick is None:
        raise HTTPException(status_code=404, detail="尚未收到任何台指期行情資料。")
    return {"status": "ok", "data": tick}


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

    max_codes = int(os.getenv("HANSTOCK_REALTIME_GROUP_MAX_CODES", "100"))
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
def get_stock_realtime(
    stock_code: str,
    subscribe: bool = Query(default=True),
) -> dict[str, Any]:
    svc = _quote_service_or_503()
    code = _split_codes(stock_code)
    if len(code) != 1:
        raise HTTPException(status_code=422, detail="請提供一個股票代號。")
    code_value = code[0]

    subscription = svc.ensure_stock_subscriptions([code_value]) if subscribe else None
    quote = svc.get_stock_quote(code_value)
    return {
        "status": "ok" if quote else "waiting",
        "subscription": subscription,
        "data": _stock_payload(code_value, quote),
    }


@app.get("/api/groups")
def list_groups(include_stocks: bool = Query(default=False)) -> dict[str, Any]:
    groups = []
    for group_name, stocks in STOCK_GROUPS.items():
        item: dict[str, Any] = {
            "group_name": group_name,
            "stock_count": len(stocks),
        }
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

    groups = []
    for group_name in group_names:
        stocks = STOCK_GROUPS[group_name]
        groups.append({
            "group_name": group_name,
            "stock_count": len(stocks),
            "stocks": [
                {"stock_code": code, "stock_name": name} for code, name in stocks
            ],
        })
    return {"keyword": keyword, "matched_groups": groups}


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


@app.post("/api/rule1/sync")
def sync_rule1(
    payload: dict[str, Any] = Body(...),
    x_hanstock_sync_token: str | None = Header(default=None),
) -> dict[str, Any]:
    expected_token = os.getenv("HANSTOCK_SYNC_TOKEN", "")
    if not expected_token:
        raise HTTPException(status_code=503, detail="伺服器尚未設定同步金鑰。")
    if x_hanstock_sync_token != expected_token:
        raise HTTPException(status_code=401, detail="同步金鑰不正確。")

    _validate_sync_payload(payload)
    _save_synced_results(payload)
    return {
        "status": "ok",
        "message": "Rule1 結果同步成功",
        "generated_at": payload.get("generated_at"),
        "total_groups": payload.get("summary", {}).get("total_groups"),
        "total_passed_records": payload.get("summary", {}).get("total_passed_records"),
    }
