"""HanStock 即時行情服務模組。

負責 Shioaji 登入、憑證啟用、行情連線、台指期訂閱，
以及斷線重連邏輯。設計為 FastAPI lifespan 內啟動的長駐服務。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import shioaji as sj

logger = logging.getLogger("hanstock.quote_service")

# 台灣時區
TW_TZ = timezone(timedelta(hours=8))


@dataclass
class QuoteState:
    """即時行情狀態追蹤。"""

    initialized: bool = False
    logged_in: bool = False
    certificate_active: bool = False
    quote_connected: bool = False
    subscribed: bool = False
    last_quote_time: Optional[str] = None
    last_tick_data: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    data_source: str = "none"
    reconnect_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "shioaji_initialized": self.initialized,
                "shioaji_logged_in": self.logged_in,
                "certificate_active": self.certificate_active,
                "quote_connected": self.quote_connected,
                "subscribed": self.subscribed,
                "last_quote_time": self.last_quote_time,
                "data_source": self.data_source,
                "reconnect_count": self.reconnect_count,
                "error_message": self.error_message,
            }

    def update_tick_time(self, tick_time: str) -> None:
        with self._lock:
            self.last_quote_time = tick_time


class QuoteService:
    """Shioaji 即時行情長駐服務。"""

    def __init__(self) -> None:
        self.api: Optional[sj.Shioaji] = None
        self.state = QuoteState()
        self._shutdown_event = threading.Event()
        self._reconnect_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # 公開方法
    # ------------------------------------------------------------------

    def startup(self) -> None:
        """同步啟動：初始化 → 登入 → 憑證 → 訂閱。

        任何步驟失敗都不會拋出例外，僅記錄錯誤狀態。
        """
        try:
            self._initialize()
            self._login()
            self._activate_ca()
            self._subscribe_futures()
        except Exception as exc:
            logger.error("即時行情啟動流程發生未預期錯誤: %s", exc)
            self.state.error_message = str(exc)

    def shutdown(self) -> None:
        """安全關閉 Shioaji 連線。"""
        self._shutdown_event.set()
        if self.api and self.state.logged_in:
            try:
                self.api.logout()
                logger.info("Shioaji 已安全登出。")
            except Exception as exc:
                logger.warning("登出時發生錯誤: %s", exc)
        self.state.logged_in = False
        self.state.quote_connected = False
        self.state.subscribed = False

    def get_health(self) -> dict[str, Any]:
        """取得行情服務健康狀態。"""
        return self.state.to_dict()

    def get_latest_tick(self) -> Optional[dict[str, Any]]:
        """取得最新一筆 tick 資料。"""
        return self.state.last_tick_data

    # ------------------------------------------------------------------
    # 內部方法
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        """建立 Shioaji 實例。"""
        logger.info("[Shioaji] 初始化中...")
        try:
            simulation = os.getenv("SHIOAJI_SIMULATION", "false").lower() == "true"
            self.api = sj.Shioaji(simulation=simulation)
            self.state.initialized = True
            mode_str = "模擬模式" if simulation else "正式模式"
            logger.info("[Shioaji] 初始化成功（%s）。", mode_str)
        except Exception as exc:
            logger.error("[Shioaji] 初始化失敗: %s", exc)
            self.state.error_message = f"初始化失敗: {exc}"
            raise

    def _login(self) -> None:
        """登入永豐 Shioaji。"""
        if not self.state.initialized or self.api is None:
            return

        api_key = os.getenv("SHIOAJI_API_KEY", "")
        secret_key = os.getenv("SHIOAJI_SECRET_KEY", "")

        if not api_key or not secret_key:
            msg = "缺少 SHIOAJI_API_KEY 或 SHIOAJI_SECRET_KEY 環境變數。"
            logger.error("[Shioaji] %s", msg)
            self.state.error_message = msg
            return

        logger.info("[Shioaji] 登入中...")
        try:
            self.api.login(
                api_key=api_key,
                secret_key=secret_key,
                fetch_contract=True,
            )
            self.state.logged_in = True
            self.state.error_message = None
            logger.info("[Shioaji] 登入成功。")
        except Exception as exc:
            logger.error("[Shioaji] 登入失敗: %s", exc)
            self.state.error_message = f"登入失敗: {exc}"
            self.state.logged_in = False

    def _activate_ca(self) -> None:
        """啟用電子憑證（下單用，行情不一定需要，但若有設定就啟用）。"""
        if not self.state.logged_in or self.api is None:
            return

        ca_path = os.getenv("SHIOAJI_CA_PATH", "")
        ca_passwd = os.getenv("SHIOAJI_CA_PASSWD", "")
        person_id = os.getenv("SHIOAJI_PERSON_ID", "")

        if not ca_path or not ca_passwd or not person_id:
            logger.info("[Shioaji] 未設定憑證相關環境變數，跳過憑證啟用。")
            return

        logger.info("[Shioaji] 啟用電子憑證中...")
        try:
            result = self.api.activate_ca(
                ca_path=ca_path,
                ca_passwd=ca_passwd,
                person_id=person_id,
            )
            if result:
                self.state.certificate_active = True
                logger.info("[Shioaji] 電子憑證啟用成功。")
            else:
                logger.warning("[Shioaji] 電子憑證啟用回傳 False。")
                self.state.error_message = "憑證啟用回傳 False"
        except Exception as exc:
            logger.error("[Shioaji] 電子憑證啟用失敗: %s", exc)
            self.state.error_message = f"憑證啟用失敗: {exc}"

    def _subscribe_futures(self) -> None:
        """訂閱台指期近月合約即時行情。"""
        if not self.state.logged_in or self.api is None:
            return

        # 設定事件回呼
        self._setup_event_callback()
        # 設定行情回呼
        self._setup_quote_callback()

        # 取得台指期近月合約
        target_code = os.getenv("SHIOAJI_FUTURES_CODE", "TXFR1")
        logger.info("[Shioaji] 訂閱台指期行情: %s", target_code)

        try:
            contract = self.api.Contracts.Futures.TXF[target_code]
            if contract is None:
                logger.error("[Shioaji] 找不到合約: %s", target_code)
                self.state.error_message = f"找不到合約: {target_code}"
                return

            self.api.subscribe(
                contract,
                quote_type=sj.QuoteType.Tick,
            )
            self.state.subscribed = True
            self.state.quote_connected = True
            self.state.data_source = f"shioaji_realtime_{target_code}"
            logger.info("[Shioaji] 訂閱成功: %s", target_code)
        except Exception as exc:
            logger.error("[Shioaji] 訂閱失敗: %s", exc)
            self.state.error_message = f"訂閱失敗: {exc}"
            self.state.subscribed = False

    def _setup_event_callback(self) -> None:
        """設定 Solace 連線事件回呼。"""
        if self.api is None:
            return

        @self.api.quote.on_event
        def _event_callback(resp_code: int, event_code: int, info: str, event: str):
            logger.info(
                "[Shioaji][Event] code=%d, event_code=%d, info=%s, event=%s",
                resp_code, event_code, info, event,
            )

            # 連線狀態追蹤
            if event_code == 0:
                # SESSION_UP
                self.state.quote_connected = True
                logger.info("[Shioaji] 行情連線已建立。")
            elif event_code == 1:
                # SESSION_DOWN
                self.state.quote_connected = False
                logger.warning("[Shioaji] 行情連線已斷開。")
            elif event_code == 12:
                # RECONNECTING
                self.state.quote_connected = False
                self.state.reconnect_count += 1
                logger.warning("[Shioaji] 行情連線重連中（第 %d 次）...", self.state.reconnect_count)
            elif event_code == 13:
                # RECONNECTED
                self.state.quote_connected = True
                logger.info("[Shioaji] 行情連線重連成功。")
            elif event_code == 16:
                # SUBSCRIPTION_OK
                logger.info("[Shioaji] 訂閱/取消訂閱操作成功。")

    def _setup_quote_callback(self) -> None:
        """設定台指期 Tick 行情回呼。"""
        if self.api is None:
            return

        @self.api.on_tick_fop_v1()
        def _tick_callback(exchange: sj.Exchange, tick: sj.TickFOPv1):
            now = datetime.now(TW_TZ).isoformat(timespec="seconds")
            tick_time = str(tick.datetime) if hasattr(tick, "datetime") else now

            self.state.update_tick_time(tick_time)
            self.state.quote_connected = True

            # 儲存最新 tick 資料
            self.state.last_tick_data = {
                "code": tick.code,
                "close": float(tick.close),
                "volume": tick.volume,
                "total_volume": tick.total_volume,
                "tick_type": tick.tick_type,
                "high": float(tick.high),
                "low": float(tick.low),
                "open": float(tick.open),
                "price_chg": float(tick.price_chg),
                "pct_chg": float(tick.pct_chg),
                "bid_side_total_vol": tick.bid_side_total_vol,
                "ask_side_total_vol": tick.ask_side_total_vol,
                "simtrade": tick.simtrade,
                "received_at": now,
            }


# 全域單例
_service: Optional[QuoteService] = None


def get_quote_service() -> QuoteService:
    """取得全域 QuoteService 單例。"""
    global _service
    if _service is None:
        _service = QuoteService()
    return _service
