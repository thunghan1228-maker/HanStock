"""HanStock 正式 ASGI app 包裝器。

只保留台股即時行情與股票 1m/5m Hub API。
台指期、OTC 指數、Rule1、三角/VCP、資金流等舊 API 不再對外暴露。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from api_server import app
from stock_bar_bootstrap import get_resilient_stock_bars


def _normalize_stock_code(raw: str) -> str:
    code = str(raw).strip().upper()
    if not code or len(code) > 12 or not code.replace("-", "").isalnum():
        raise HTTPException(status_code=422, detail=f"股票代號格式不正確：{raw}")
    return code


def _remove_route(path: str, methods: set[str] | None = None) -> None:
    """從正式 ASGI router 移除已淘汰的舊 endpoint。"""
    wanted = {method.upper() for method in (methods or set())}
    kept = []
    for route in app.router.routes:
        route_path = getattr(route, "path", None)
        route_methods = {str(item).upper() for item in (getattr(route, "methods", None) or set())}
        if route_path == path and (not wanted or wanted.intersection(route_methods)):
            continue
        kept.append(route)
    app.router.routes[:] = kept


# Railway 重啟會清空 MarketDataHub 記憶體；正式 app 使用歷史 Kbars + 即時 Hub
# 的股票合併版，供主力副圖繼續使用。
_remove_route("/api/hub/bars1m/{stock_code}", {"GET"})
_remove_route("/api/hub/bars/{stock_code}", {"GET"})

# 已停用的高成本/非核心功能：不再讓舊前端或外部請求誤觸發相關程式。
for _obsolete_path in (
    "/api/quote/futures",
    "/api/rule1/latest",
    "/api/rule1/passed",
    "/api/rule1/sync",
    "/api/hub/official/tpex-institutional-latest",
    "/api/hub/daytrade-flow-ranking",
    "/api/screener/vcp/latest",
    "/api/screener/vcp/run",
    "/api/screener/triangles/latest",
    "/api/screener/triangles/run",
    "/api/screener/triangles/intraday/latest",
):
    _remove_route(_obsolete_path)


@app.get("/api/hub/bars1m/{stock_code}")
def get_resilient_hub_bars_1m(stock_code: str) -> dict[str, Any]:
    """今日股票 1 分 K：歷史補齊後接續即時 Hub。"""
    code = _normalize_stock_code(stock_code)
    return get_resilient_stock_bars(code, "1m")


@app.get("/api/hub/bars/{stock_code}")
def get_resilient_hub_bars_5m(stock_code: str) -> dict[str, Any]:
    """今日股票 5 分 K：由 1 分 K 聚合並接續即時 Hub。"""
    code = _normalize_stock_code(stock_code)
    return get_resilient_stock_bars(code, "5m")
