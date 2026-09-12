"""HanStock 正式 ASGI app 包裝器。

只保留台股即時行情、櫃買指數與股票 1m/5m Hub API。
台指期、Rule1、三角/VCP、資金流等舊 API 不再對外暴露。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

import quote_service as quote_module
from otc_index import OTC_INDEX_DISPLAY_NAME, OTC_INDEX_HUB_CODE, exchange_text
from otc_index_hub import get_otc_index_hub
from otc_index_service import get_otc_index_service

logger = logging.getLogger("hanstock.otc_index_runtime")
TW_TZ = timezone(timedelta(hours=8))


def _safe_float(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return 0 if value is None else max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _quote_datetime_iso(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=TW_TZ)
        return dt.astimezone(TW_TZ).isoformat()
    if value:
        return str(value)
    return datetime.now(TW_TZ).isoformat()


def _install_otc_index_patch() -> None:
    """掛上櫃買指數即時行情：只接在 _setup_callbacks（登入/重連都會呼叫），
    不再依賴已停用的期貨訂閱流程觸發（_do_subscribe_futures 現在不會被呼叫）。
    """
    cls = quote_module.QuoteService
    if getattr(cls, "_hanstock_otc_index_patch_v1", False):
        return

    original_setup_callbacks = cls._setup_callbacks

    def patched_setup_callbacks(self: Any) -> None:
        # 原本股票 callbacks 永遠先完成；OTC index 掛載/訂閱失敗不得往外拋。
        original_setup_callbacks(self)
        api = self.api
        if api is None:
            return

        api_id = id(api)
        if getattr(self, "_otc_index_callback_api_id", None) != api_id:
            try:
                index_service = get_otc_index_service()

                def _otc_index_quote_callback(quote: Any) -> None:
                    try:
                        if not index_service.accepts_quote(quote):
                            return
                        quote_time = _quote_datetime_iso(getattr(quote, "datetime", None))
                        quote_data = {
                            "hub_code": OTC_INDEX_HUB_CODE,
                            "code": str(getattr(quote, "code", "") or "").strip().upper(),
                            "exchange": exchange_text(getattr(quote, "exchange", "OTC")),
                            "name": OTC_INDEX_DISPLAY_NAME,
                            "reference": _safe_float(getattr(quote, "reference", None)),
                            "open": _safe_float(getattr(quote, "open", None)),
                            "high": _safe_float(getattr(quote, "high", None)),
                            "low": _safe_float(getattr(quote, "low", None)),
                            "close": _safe_float(getattr(quote, "close", None)),
                            "volume": _safe_int(getattr(quote, "volume", None)),
                            "vol_sum": _safe_int(getattr(quote, "vol_sum", None)),
                            "amount_sum": _safe_float(getattr(quote, "amount_sum", None)),
                            "quote_time": quote_time,
                            "datetime": quote_time,
                            "received_at": datetime.now(TW_TZ).isoformat(),
                            "data_source": "shioaji_realtime_index",
                        }
                        get_otc_index_hub().on_quote(quote_data)
                        self.state.quote_connected = True
                    except Exception as exc:
                        logger.debug("[OTC Index] quote callback 處理失敗: %s", exc)

                # Shioaji 1.7 官方同時提供 setter 與 decorator；優先用 setter，
                # 舊/差異版再退回 decorator。兩者都不可用時只停用 index，不影響股票。
                setter = getattr(api, "set_on_quote_idx_v1_callback", None)
                if callable(setter):
                    setter(_otc_index_quote_callback)
                else:
                    decorator_factory = getattr(api, "on_quote_idx_v1", None)
                    if not callable(decorator_factory):
                        raise AttributeError("Shioaji API 不支援 QuoteIdxV1 callback")
                    decorator_factory()(_otc_index_quote_callback)

                self._otc_index_callback_api_id = api_id
                logger.info("[OTC Index] QuoteIdxV1 callback 已掛載 (api_id=%s)", api_id)
            except Exception as exc:
                get_otc_index_hub().set_subscribed(False, f"Index callback 掛載失敗: {exc}")
                logger.warning("[OTC Index] callback 掛載失敗（原股票繼續）: %s", exc)

        previous_api_id = getattr(self, "_otc_index_subscription_api_id", None)
        if previous_api_id == api_id:
            return
        try:
            service = get_otc_index_service()
            ok = service.subscribe(
                api,
                bootstrap=True,
                force_resolve=previous_api_id is not None and previous_api_id != api_id,
            )
            if ok:
                self._otc_index_subscription_api_id = api_id
        except Exception as exc:
            logger.warning("[OTC Index] 自動訂閱例外（不影響股票）: %s", exc)

    cls._setup_callbacks = patched_setup_callbacks
    cls._hanstock_otc_index_patch_v1 = True
    logger.info("[OTC Index] QuoteService runtime patch 已安裝")


_install_otc_index_patch()

# patch 完成後才載入原 FastAPI app；其 lifespan 啟動 QuoteService 時即會自動套用。
from api_server import app  # noqa: E402
from stock_bar_bootstrap import get_resilient_stock_bars  # noqa: E402


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


# 正式首頁直接使用精簡版前端，避免舊的首頁/快取路由再次出現已移除功能。
_remove_route("/", {"GET"})
_remove_route("/hub-dashboard", {"GET"})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def production_homepage() -> HTMLResponse:
    """正式首頁只展示台股即時行情與主力副圖。"""
    path = Path(__file__).parent / "web" / "index.html"
    if not path.exists():
        return HTMLResponse("<h1>HanStock</h1><p>首頁資源暫時無法載入。</p>", status_code=503)
    return HTMLResponse(
        path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


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
