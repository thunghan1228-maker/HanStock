"""HanStock 網站 API。

提供族群資料與最新 Rule1 結果，供 hanstock.xyz、LINE Bot 或 App 使用。
啟動方式：py run_api.py

v1.1.0: 新增 Shioaji 即時行情服務串接。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from config import SHIOAJI_QUOTE_ENABLED
from read_rule1_results import RESULT_PATH, load_rule1_results
from stock_groups import STOCK_GROUPS, resolve_group_names

# ─── 日誌設定 ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hanstock.api")

API_VERSION = "1.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

TW_TZ = timezone(timedelta(hours=8))


# ─── FastAPI Lifespan ───

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命週期管理：啟動時初始化 Shioaji，關閉時安全登出。"""
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

    yield  # 應用程式運行中

    # 關閉
    if quote_svc is not None:
        logger.info("＝＝＝＝ 關閉 Shioaji 即時行情服務 ＝＝＝＝")
        try:
            quote_svc.shutdown()
        except Exception as exc:
            logger.warning("Shioaji 關閉時發生錯誤: %s", exc)


# ─── 輔助函式 ───

def _allowed_origins() -> list[str]:
    """從環境變數讀取允許跨網域的網站來源。"""
    raw = os.getenv(
        "HANSTOCK_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,https://hanstock.xyz,https://www.hanstock.xyz",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def _flatten_passed_stocks(results: dict[str, Any]) -> list[dict[str, Any]]:
    """把分族群的 Rule1 結果展平成股票清單。"""
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
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(RESULT_PATH)


# ─── FastAPI App ───

app = FastAPI(
    title="HanStock API",
    description="HanStock 台灣股票族群與 Rule1 選股結果 API，含台指期即時行情",
    version=API_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ─── 路由 ───

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    """提供可直接用瀏覽器查看的 HanStock 控制台。"""
    dashboard_path = Path(__file__).parent / "web" / "index.html"

    if not dashboard_path.exists():
        return "<h1>HanStock API</h1><p>找不到 web/index.html。</p>"

    return dashboard_path.read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    """服務健康檢查（含 Shioaji 即時行情狀態）。"""
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")

    base = {
        "api_status": "ok",
        "service": "HanStock API",
        "version": API_VERSION,
        "server_time": now,
        "rule1_result_exists": RESULT_PATH.exists(),
        "group_count": len(STOCK_GROUPS),
    }

    # 加入 Shioaji 行情狀態
    if SHIOAJI_QUOTE_ENABLED:
        try:
            from quote_service import get_quote_service
            svc = get_quote_service()
            base.update(svc.get_health())
        except Exception:
            base.update({
                "shioaji_initialized": False,
                "shioaji_logged_in": False,
                "certificate_active": False,
                "quote_connected": False,
                "subscribed": False,
                "last_quote_time": None,
                "data_source": "error",
            })
    else:
        base.update({
            "shioaji_initialized": False,
            "shioaji_logged_in": False,
            "certificate_active": False,
            "quote_connected": False,
            "subscribed": False,
            "last_quote_time": None,
            "data_source": "disabled",
        })

    return base


@app.get("/api/quote/futures")
def get_futures_quote() -> dict[str, Any]:
    """取得台指期最新即時行情。"""
    if not SHIOAJI_QUOTE_ENABLED:
        raise HTTPException(status_code=503, detail="即時行情服務未啟用。")

    from quote_service import get_quote_service
    svc = get_quote_service()

    if not svc.state.logged_in:
        raise HTTPException(status_code=503, detail="Shioaji 尚未登入。")

    tick = svc.get_latest_tick()
    if tick is None:
        raise HTTPException(status_code=404, detail="尚未收到任何行情資料。")

    return {
        "status": "ok",
        "data": tick,
    }


@app.get("/api/groups")
def list_groups(
    include_stocks: bool = Query(default=False),
) -> dict[str, Any]:
    """取得全部族群；可選擇是否包含成分股。"""
    groups = []

    for group_name, stocks in STOCK_GROUPS.items():
        item: dict[str, Any] = {
            "group_name": group_name,
            "stock_count": len(stocks),
        }
        if include_stocks:
            item["stocks"] = [
                {"stock_code": code, "stock_name": name}
                for code, name in stocks
            ]
        groups.append(item)

    return {"group_count": len(groups), "groups": groups}


@app.get("/api/groups/{keyword}")
def get_groups_by_keyword(keyword: str) -> dict[str, Any]:
    """以族群名稱或股票代號查詢所屬族群。"""
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
                "stocks": [
                    {"stock_code": code, "stock_name": name}
                    for code, name in stocks
                ],
            }
        )

    return {"keyword": keyword, "matched_groups": groups}


@app.get("/api/rule1/latest")
def latest_rule1(
    passed_only: bool = Query(default=False),
) -> dict[str, Any]:
    """取得最近一次全族群 Rule1 掃描結果。"""
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
    """只取得符合 Rule1 的股票清單。"""
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
    """由本機 HanStock 安全上傳最新 Rule1 結果。"""
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
