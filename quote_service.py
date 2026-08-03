"""HanStock 即時行情服務模組。

負責 Shioaji 登入、憑證啟用、行情連線、台指期訂閱，
以及斷線重連邏輯。設計為 FastAPI lifespan 內啟動的長駐服務。

狀態設定原則：
- subscribed = True：僅在收到 Event Code 16（SUBSCRIPTION_OK）或首筆 Tick 後設定
- quote_connected = True：僅在收到 Event Code 0（SESSION_UP）或實際收到行情後設定
- 不在 api.subscribe() 呼叫後立即設定任何連線狀態
"""

from __future__ import annotations

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

# 重連設定
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_BASE_INTERVAL = 5  # 秒，指數退避基底
RECONNECT_MAX_INTERVAL = 300  # 秒，最大間隔 5 分鐘


@dataclass
class QuoteState:
    """即時行情狀態追蹤。"""

    initialized: bool = False
    logged_in: bool = False
    certificate_active: bool = False
    quote_connected: bool = False
    subscribed: bool = False
    last_quote_time: Optional[str] = None
    last_quote_timestamp: Optional[float] = None  # Unix timestamp for age calc
    last_tick_data: Optional[dict[str, Any]] = None
    last_event: Optional[str] = None
    current_contract: Optional[str] = None
    error_message: Optional[str] = None
    data_source: str = "none"
    reconnect_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            # 計算行情年齡
            quote_age_seconds: Optional[float] = None
            quote_stale = False
            if self.last_quote_timestamp is not None:
                quote_age_seconds = round(time.time() - self.last_quote_timestamp, 1)
                # 超過 60 秒沒有新行情視為 stale
                quote_stale = quote_age_seconds > 60.0

            return {
                "shioaji_initialized": self.initialized,
                "shioaji_logged_in": self.logged_in,
                "certificate_active": self.certificate_active,
                "quote_connected": self.quote_connected,
                "subscribed": self.subscribed,
                "last_quote_time": self.last_quote_time,
                "quote_age_seconds": quote_age_seconds,
                "quote_stale": quote_stale,
                "current_contract": self.current_contract,
                "last_event": self.last_event,
                "data_source": self.data_source,
                "reconnect_count": self.reconnect_count,
                "error_message": self.error_message,
            }

    def update_tick(self, tick_time: str, tick_data: dict[str, Any]) -> None:
        with self._lock:
            self.last_quote_time = tick_time
            self.last_quote_timestamp = time.time()
            self.last_tick_data = tick_data
            # 收到實際行情 → 確認連線與訂閱
            self.quote_connected = True
            self.subscribed = True

    def set_event(self, event_str: str) -> None:
        with self._lock:
            self.last_event = event_str


class QuoteService:
    """Shioaji 即時行情長駐服務。"""

    def __init__(self) -> None:
        self.api: Optional[sj.Shioaji] = None
        self.state = QuoteState()
        self._shutdown_event = threading.Event()
        self._reconnect_thread: Optional[threading.Thread] = None
        self._target_code: str = os.getenv("SHIOAJI_FUTURES_CODE", "TXFR1")

    # ------------------------------------------------------------------
    # 公開方法
    # ------------------------------------------------------------------

    def startup(self) -> None:
        """同步啟動：初始化 → 登入 → 憑證 → 訂閱。

        任何步驟失敗都不會拋出例外，僅記錄錯誤狀態。
        API 仍能正常運作。
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

        # 等待重連執行緒結束
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            self._reconnect_thread.join(timeout=5)

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
        with self.state._lock:
            return self.state.last_tick_data

    # ------------------------------------------------------------------
    # 內部方法：初始化流程
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
        """訂閱台指期近月合約即時行情。

        注意：呼叫 api.subscribe() 後不立即設定 subscribed=True，
        而是等待 Event Code 16 或首筆 Tick 回呼才確認。
        """
        if not self.state.logged_in or self.api is None:
            return

        # 設定事件回呼
        self._setup_event_callback()
        # 設定行情回呼
        self._setup_quote_callback()

        self._do_subscribe()

    def _do_subscribe(self) -> None:
        """執行訂閱動作（可被重連流程重複呼叫）。"""
        if self.api is None:
            return

        logger.info("[Shioaji] 訂閱台指期行情: %s", self._target_code)

        try:
            contract = self.api.Contracts.Futures.TXF[self._target_code]
            if contract is None:
                logger.error("[Shioaji] 找不到合約: %s", self._target_code)
                self.state.error_message = f"找不到合約: {self._target_code}"
                return

            self.state.current_contract = self._target_code
            self.state.data_source = f"shioaji_realtime_{self._target_code}"

            # 呼叫 subscribe，但不立即設定 subscribed/quote_connected
            # 等待 Event Code 16 或首筆 Tick 確認
            self.api.subscribe(
                contract,
                quote_type=sj.QuoteType.Tick,
            )
            logger.info("[Shioaji] subscribe() 已呼叫，等待確認...")
        except Exception as exc:
            logger.error("[Shioaji] 訂閱失敗: %s", exc)
            self.state.error_message = f"訂閱失敗: {exc}"
            self.state.subscribed = False

    # ------------------------------------------------------------------
    # 內部方法：回呼
    # ------------------------------------------------------------------

    def _setup_event_callback(self) -> None:
        """設定 Solace 連線事件回呼。"""
        if self.api is None:
            return

        @self.api.quote.on_event
        def _event_callback(resp_code: int, event_code: int, info: str, event: str):
            event_str = f"code={event_code}, resp={resp_code}, info={info}, event={event}"
            self.state.set_event(event_str)
            logger.info("[Shioaji][Event] %s", event_str)

            if event_code == 0:
                # SESSION_UP - 連線建立
                self.state.quote_connected = True
                logger.info("[Shioaji] 行情連線已建立（SESSION_UP）。")

            elif event_code == 1:
                # SESSION_DOWN - 連線斷開
                self.state.quote_connected = False
                self.state.subscribed = False
                logger.warning("[Shioaji] 行情連線已斷開（SESSION_DOWN）。")
                self._trigger_reconnect()

            elif event_code == 2:
                # CONNECT_FAILED
                self.state.quote_connected = False
                self.state.subscribed = False
                logger.error("[Shioaji] 行情連線失敗（CONNECT_FAILED）。")
                self._trigger_reconnect()

            elif event_code == 12:
                # RECONNECTING - Solace 自動重連中
                self.state.quote_connected = False
                self.state.subscribed = False
                self.state.reconnect_count += 1
                logger.warning(
                    "[Shioaji] 行情連線重連中（RECONNECTING，第 %d 次）...",
                    self.state.reconnect_count,
                )

            elif event_code == 13:
                # RECONNECTED - Solace 自動重連成功
                self.state.quote_connected = True
                logger.info("[Shioaji] 行情連線重連成功（RECONNECTED）。")
                # 重連成功後重新訂閱
                self._do_subscribe()

            elif event_code == 16:
                # SUBSCRIPTION_OK - 訂閱確認成功
                self.state.subscribed = True
                logger.info("[Shioaji] 訂閱確認成功（SUBSCRIPTION_OK）。")

    def _setup_quote_callback(self) -> None:
        """設定台指期 Tick 行情回呼。"""
        if self.api is None:
            return

        @self.api.on_tick_fop_v1()
        def _tick_callback(exchange: sj.Exchange, tick: sj.TickFOPv1):
            now = datetime.now(TW_TZ).isoformat(timespec="seconds")

            # 嘗試取得 tick 的時間
            try:
                tick_time = str(tick.datetime) if hasattr(tick, "datetime") else now
            except Exception:
                tick_time = now

            tick_data = {
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
                "tick_time": tick_time,
                "received_at": now,
            }

            # 更新狀態（含確認 subscribed 和 quote_connected）
            self.state.update_tick(tick_time, tick_data)

    # ------------------------------------------------------------------
    # 內部方法：斷線重連
    # ------------------------------------------------------------------

    def _trigger_reconnect(self) -> None:
        """觸發斷線重連流程（在背景執行緒中）。"""
        if self._shutdown_event.is_set():
            return

        # 避免重複啟動重連
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            logger.info("[Shioaji] 重連執行緒已在運行，跳過。")
            return

        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            name="shioaji-reconnect",
            daemon=True,
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        """斷線重連迴圈：指數退避，有次數上限。"""
        attempt = 0

        while attempt < MAX_RECONNECT_ATTEMPTS and not self._shutdown_event.is_set():
            attempt += 1

            # 指數退避間隔
            interval = min(
                RECONNECT_BASE_INTERVAL * (2 ** (attempt - 1)),
                RECONNECT_MAX_INTERVAL,
            )
            logger.info(
                "[Shioaji] 重連嘗試 %d/%d，等待 %d 秒...",
                attempt, MAX_RECONNECT_ATTEMPTS, interval,
            )

            # 等待間隔（可被 shutdown 中斷）
            if self._shutdown_event.wait(timeout=interval):
                logger.info("[Shioaji] 收到關閉信號，停止重連。")
                return

            # 如果 Solace 已自動重連成功，就不需要重新登入
            if self.state.quote_connected:
                logger.info("[Shioaji] 連線已恢復，停止重連迴圈。")
                return

            # 嘗試重新登入
            logger.info("[Shioaji] 嘗試重新登入...")
            try:
                # 先嘗試登出舊連線
                if self.api:
                    try:
                        self.api.logout()
                    except Exception:
                        pass

                self.state.logged_in = False
                self.state.quote_connected = False
                self.state.subscribed = False

                # 重新初始化
                self._initialize()
                self._login()

                if not self.state.logged_in:
                    logger.warning("[Shioaji] 重連登入失敗，繼續嘗試...")
                    continue

                self._activate_ca()

                # 重新設定回呼並訂閱
                self._setup_event_callback()
                self._setup_quote_callback()
                self._do_subscribe()

                logger.info("[Shioaji] 重連流程完成，等待行情確認...")
                # 等待一段時間看是否收到行情
                time.sleep(10)
                if self.state.subscribed or self.state.quote_connected:
                    logger.info("[Shioaji] 重連成功！")
                    return

            except Exception as exc:
                logger.error("[Shioaji] 重連嘗試 %d 失敗: %s", attempt, exc)
                self.state.error_message = f"重連失敗 ({attempt}/{MAX_RECONNECT_ATTEMPTS}): {exc}"

        if attempt >= MAX_RECONNECT_ATTEMPTS:
            msg = f"已達重連上限 ({MAX_RECONNECT_ATTEMPTS} 次)，停止重連。"
            logger.error("[Shioaji] %s", msg)
            self.state.error_message = msg


# 全域單例
_service: Optional[QuoteService] = None


def get_quote_service() -> QuoteService:
    """取得全域 QuoteService 單例。"""
    global _service
    if _service is None:
        _service = QuoteService()
    return _service
