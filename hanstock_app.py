"""HanStock 正式 ASGI app 包裝器。

不修改既有 quote_service.py / api_server.py 的穩定股票與期貨流程；
在載入原 app 前，以極小 monkey patch 掛上 Shioaji 1.7 Index Quote 訂閱，
並在原 FastAPI app 載入後增加 OTC index Hub REST 端點。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Query

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
