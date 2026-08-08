"""HanStock 正式 ASGI app 包裝器。

不修改既有 quote_service.py / api_server.py 的穩定股票與期貨流程；
在載入原 app 前，以極小 monkey patch 掛上 Shioaji 1.7 Index Quote 訂閱，
並在原 FastAPI app 載入後增加 OTC index Hub REST 端點、股票期貨近月即時行情，
以及可跨重啟自動補齊今日歷史 Kbars 的個股 1m/5m Hub 端點。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query

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
    cls = quote_module.QuoteService
    if getattr(cls, "_hanstock_otc_index_patch_v1", False):
        return

    original_setup_callbacks = cls._setup_callbacks
    original_subscribe_futures = cls._do_subscribe_futures

    def patched_setup_callbacks(self: Any) -> None:
        # 原本期貨/股票 callbacks 永遠先完成；OTC index 掛載失敗不得往外拋。
        original_setup_callbacks(self)
        api = self.api
        if api is None:
            return

        api_id = id(api)
        if getattr(self, "_otc_index_callback_api_id", None) == api_id:
            return

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
            # 舊/差異版再退回 decorator。兩者都不可用時只停用 index，不影響股票期貨。
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
            logger.warning("[OTC Index] callback 掛載失敗（原股票/期貨繼續）: %s", exc)

    def patched_subscribe_futures(self: Any) -> None:
        # 原本台指期流程永遠先跑；OTC index 失敗也不能影響期貨。
        original_subscribe_futures(self)
        api = self.api
        if api is None or not self.state.logged_in:
            return
        try:
            api_id = id(api)
            previous_api_id = getattr(self, "_otc_index_subscription_api_id", None)
            service = get_otc_index_service()
            ok = service.subscribe(
                api,
                bootstrap=True,
                force_resolve=previous_api_id is not None and previous_api_id != api_id,
            )
            if ok:
                self._otc_index_subscription_api_id = api_id
        except Exception as exc:
            logger.warning("[OTC Index] 自動訂閱例外（不影響股票/期貨）: %s", exc)

    cls._setup_callbacks = patched_setup_callbacks
    cls._do_subscribe_futures = patched_subscribe_futures
    cls._hanstock_otc_index_patch_v1 = True
    logger.info("[OTC Index] QuoteService runtime patch 已安裝")


_install_otc_index_patch()

# patch 完成後才載入原 FastAPI app；其 lifespan 啟動 QuoteService 時即會自動套用。
from api_server import app  # noqa: E402
from stock_bar_bootstrap import get_resilient_stock_bars  # noqa: E402
from stock_futures_service import get_stock_futures_quote_service  # noqa: E402


def _normalize_stock_code(raw: str) -> str:
    code = str(raw).strip().upper()
    if not code or len(code) > 12 or not code.replace("-", "").isalnum():
        raise HTTPException(status_code=422, detail=f"股票代號格式不正確：{raw}")
    return code


def _parse_underlyings(raw: str) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for item in str(raw).split(","):
        code = _normalize_stock_code(item)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        raise HTTPException(status_code=422, detail="underlyings 不可為空")
    # 單次 API 可接完整族群；實際 Shioaji 即時訂閱會由 stock_futures_service
    # 分散到多條專用連線，每條仍嚴格低於官方 200 訂閱上限。
    if len(codes) > 500:
        raise HTTPException(status_code=422, detail="單次最多查詢 500 檔股票期貨")
    return codes


def _remove_get_route(path: str) -> None:
    """移除 api_server.py 舊的純記憶體 GET route，避免同路徑重複。"""
    kept = []
    for route in app.router.routes:
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and "GET" in methods:
            continue
        kept.append(route)
    app.router.routes[:] = kept


# Railway 重啟會清空 MarketDataHub 記憶體，因此正式 ASGI app 以同一 URL
# 換成「Shioaji 今日歷史 Kbars + 即時 Hub」合併版。前端與既有消費者無需改 URL。
_remove_get_route("/api/hub/bars1m/{stock_code}")
_remove_get_route("/api/hub/bars/{stock_code}")


@app.get("/api/hub/bars1m/{stock_code}")
def get_resilient_hub_bars_1m(stock_code: str) -> dict[str, Any]:
    """今日 1 分 K：重啟後自動歷史補齊，並接續即時 Tick。"""
    code = _normalize_stock_code(stock_code)
    return get_resilient_stock_bars(code, "1m")


@app.get("/api/hub/bars/{stock_code}")
def get_resilient_hub_bars_5m(stock_code: str) -> dict[str, Any]:
    """今日 5 分 K：由已補齊 1 分 K 聚合並與即時 Hub 合併。"""
    code = _normalize_stock_code(stock_code)
    return get_resilient_stock_bars(code, "5m")


@app.get("/api/hub/stock-futures")
def get_stock_futures_quotes(
    underlyings: str = Query(..., description="股票標的代號，逗號分隔，例如 2330,2454"),
    mode: str = Query(default="regular", description="regular=一般股票期貨；mini=小型股票期貨"),
    subscribe: bool = Query(default=True, description="是否確保訂閱近月 QuoteFOPv1 即時行情"),
) -> dict[str, Any]:
    """股票期貨即時行情：永豐 Shioaji FOP，固定自動追蹤 R1 近月。"""
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"regular", "mini"}:
        raise HTTPException(status_code=422, detail="mode 只能是 regular 或 mini")
    codes = _parse_underlyings(underlyings)
    service = get_stock_futures_quote_service()
    quote_service = quote_module.get_quote_service()
    return service.get_quotes(
        quote_service,
        codes,
        normalized_mode,  # type: ignore[arg-type]
        subscribe=subscribe,
    )


@app.get("/api/hub/stock-futures/status")
def get_stock_futures_status() -> dict[str, Any]:
    """股票期貨專用行情通道狀態；不與現貨/台指期 latest tick 混用。"""
    service = get_stock_futures_quote_service()
    return {
        "status": "ok",
        "data": service.status(quote_module.get_quote_service()),
    }


@app.get("/api/hub/index/otc/status")
def get_otc_index_status() -> dict[str, Any]:
    """櫃買指數 Hub 狀態與最新 Quote。"""
    hub = get_otc_index_hub()
    return {
        "status": "ok",
        "data": hub.get_status(),
        "quote": hub.get_latest_quote(),
    }


@app.get("/api/hub/index/otc/bars")
def get_otc_index_bars(
    include_current: bool = Query(
        default=False,
        description="是否包含尚未收棒的目前 5 分 K；策略預設 false",
    ),
) -> dict[str, Any]:
    """取得櫃買指數今日正式 5 分 K（破三五使用）。"""
    hub = get_otc_index_hub()
    bars = hub.get_bars_5m(include_current=include_current)
    return {
        "status": "ok",
        "code": OTC_INDEX_HUB_CODE,
        "name": OTC_INDEX_DISPLAY_NAME,
        "interval": "5m",
        "include_current": include_current,
        "bar_count": len(bars),
        "bars": bars,
        "hub": hub.get_status(),
    }


@app.get("/api/hub/index/otc/bars1m")
def get_otc_index_bars_1m(
    include_current: bool = Query(default=True),
) -> dict[str, Any]:
    """取得櫃買指數今日正式 1 分 K（驗證與未來策略使用）。"""
    hub = get_otc_index_hub()
    bars = hub.get_bars_1m(include_current=include_current)
    return {
        "status": "ok",
        "code": OTC_INDEX_HUB_CODE,
        "name": OTC_INDEX_DISPLAY_NAME,
        "interval": "1m",
        "include_current": include_current,
        "bar_count": len(bars),
        "bars": bars,
        "hub": hub.get_status(),
    }
