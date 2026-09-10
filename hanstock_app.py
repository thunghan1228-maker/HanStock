"""HanStock 正式 ASGI app 包裝器。

只保留台股即時行情與股票 1m/5m Hub API。
台指期與 OTC 指數 runtime patch 已移除，避免啟動時建立不需要的行情訂閱。
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


def _remove_get_route(path: str) -> None:
    """移除 api_server.py 舊的純記憶體 GET route，避免同一路徑重複。"""
    kept = []
    for route in app.router.routes:
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and "GET" in methods:
            continue
        kept.append(route)
    app.router.routes[:] = kept


# Railway 重啟會清空 MarketDataHub 記憶體；正式 app 使用歷史 Kbars + 即時 Hub
# 的股票合併版，供主力副圖繼續使用。
_remove_get_route("/api/hub/bars1m/{stock_code}")
_remove_get_route("/api/hub/bars/{stock_code}")


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
