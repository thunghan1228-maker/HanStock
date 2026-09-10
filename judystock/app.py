"""Judy Stock API。

以 HanStock 的族群與 Rule1 資料為基礎，拿掉即時行情與永豐證券 API 等進階
功能後的簡化版網站。這個資料夾完全獨立部署，跟 HanStock 本身互不影響。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from read_rule1_results import RESULT_PATH, load_rule1_results
from stock_groups import STOCK_GROUPS, resolve_group_names

APP_VERSION = "1.0.0"

app = FastAPI(title="Judy Stock API", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
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


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    dashboard_path = Path(__file__).parent / "web" / "index.html"
    return dashboard_path.read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "api_status": "ok",
        "service": "Judy Stock API",
        "version": APP_VERSION,
        "rule1_result_exists": RESULT_PATH.exists(),
        "group_count": len(STOCK_GROUPS),
    }


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
    """讓本機掃描結果可以選擇性同步上來；沒設定金鑰時直接關閉這個功能。"""
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
