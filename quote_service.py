"""HanStock 即時行情服務模組。

負責 Shioaji 登入、台指期與台股即時行情訂閱、行情快取，
以及斷線重連邏輯。設計為 FastAPI lifespan 內啟動的長駐服務。

狀態設定原則：
- futures subscribed = True：僅在收到期貨 Event Code 16 或首筆期貨 Tick 後設定
- quote_connected = True：僅在收到 SESSION_UP 或實際行情後設定
- 台股採動態訂閱：網站查詢股票或族群時才訂閱，使用 LRU 控制訂閱數
- 不以 snapshots/ticks/kbars 輪詢取代盤中即時行情
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import shioaji as sj

logger = logging.getLogger("hanstock.quote_service")

TW_TZ = timezone(timedelta(hours=8))

MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_BASE_INTERVAL = 5
RECONNECT_MAX_INTERVAL = 300
DEFAULT_STALE_SECONDS = 60.0
DEFAULT_STOCK_SUBSCRIPTION_LIMIT = 150  # 官方總上限 200，預留期貨與未來其他行情


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_tick_datetime(value: Any, fallback: str) -> str:
    """把 Shioaji datetime（datetime 或 tuple）轉成含台灣時區的 ISO 字串。"""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=TW_TZ)
        return value.astimezone(TW_TZ).isoformat()

    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            microsecond = int(value[6]) if len(value) > 6 else 0
            parsed = datetime(
                int(value[0]), int(value[1]), int(value[2]),
                int(value[3]), int(value[4]), int(value[5]), microsecond,
                tzinfo=TW_TZ,
            )
            return parsed.isoformat()
        except (TypeError, ValueError):
            pass

    if value:
        return str(value)
    return fallback


@dataclass
class QuoteState:
    """台指期即時行情狀態追蹤。"""

    initialized: bool = False
    logged_in: bool = False
    certificate_active: bool = False
    quote_connected: bool = False
    subscribed: bool = False
    last_quote_time: Optional[str] = None
    last_quote_timestamp: Optional[float] = None
    last_tick_data: Optional[dict[str, Any]] = None
    last_event: Optional[str] = None
    current_contract: Optional[str] = None
    error_message: Optional[str] = None
    data_source: str = "none"
    reconnect_count: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def to_dict(self, stale_seconds: float = DEFAULT_STALE_SECONDS) -> dict[str, Any]:
        with self._lock:
            quote_age_seconds: Optional[float] = None
            quote_stale = False
            if self.last_quote_timestamp is not None:
                quote_age_seconds = round(time.time() - self.last_quote_timestamp, 1)
                quote_stale = quote_age_seconds > stale_seconds

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
            self.last_tick_data = dict(tick_data)
            self.quote_connected = True
            self.subscribed = True

    def set_event(self, event_str: str) -> None:
        with self._lock:
            self.last_event = event_str


class QuoteService:
    """Shioaji 即時行情長駐服務（台指期 + 動態台股）。"""

    def __init__(self) -> None:
        self.api: Optional[sj.Shioaji] = None
        self.state = QuoteState()
        self._shutdown_event = threading.Event()
        self._reconnect_thread: Optional[threading.Thread] = None
        self._target_code = os.getenv("SHIOAJI_FUTURES_CODE", "TXFR1").strip() or "TXFR1"
        self._resolved_futures_code: Optional[str] = None
        self._stale_seconds = _env_float(
            "SHIOAJI_QUOTE_STALE_SECONDS", DEFAULT_STALE_SECONDS, 5.0, 3600.0
        )

        self._callbacks_api_id: Optional[int] = None
        self._stock_lock = threading.RLock()
        self._stock_ticks: dict[str, dict[str, Any]] = {}
        self._stock_tick_timestamps: dict[str, float] = {}
        self._stock_contracts: dict[str, Any] = {}
        self._stock_subscriptions: OrderedDict[str, float] = OrderedDict()
        self._stock_errors: dict[str, str] = {}
        self._stock_subscription_limit = _env_int(
            "SHIOAJI_STOCK_MAX_SUBSCRIPTIONS",
            DEFAULT_STOCK_SUBSCRIPTION_LIMIT,
            1,
            190,
        )

    # ------------------------------------------------------------------
    # 公開方法
    # ------------------------------------------------------------------

    def startup(self) -> None:
        """同步啟動：初始化 → 登入 → 憑證 → 設定回呼 → 訂閱台指期。"""
        try:
            self._initialize()
            self._login()
            self._activate_ca()
            self._setup_callbacks()
            self._do_subscribe_futures()
            self._subscribe_bootstrap_stocks()
        except Exception as exc:
            logger.error("即時行情啟動流程發生未預期錯誤: %s", exc)
            self.state.error_message = str(exc)

    def shutdown(self) -> None:
        """安全關閉 Shioaji 連線。"""
        self._shutdown_event.set()
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
        """取得台指期行情服務健康狀態。"""
        return self.state.to_dict(self._stale_seconds)

    def get_latest_tick(self) -> Optional[dict[str, Any]]:
        """取得最新一筆台指期 tick。"""
        with self.state._lock:
            return dict(self.state.last_tick_data) if self.state.last_tick_data else None

    def get_stock_health(self) -> dict[str, Any]:
        """取得台股動態訂閱健康狀態。"""
        with self._stock_lock:
            timestamps = list(self._stock_tick_timestamps.values())
            latest_ts = max(timestamps) if timestamps else None
            age = round(time.time() - latest_ts, 1) if latest_ts else None
            return {
                "enabled": self.state.logged_in,
                "active_subscription_count": len(self._stock_subscriptions),
                "subscription_limit": self._stock_subscription_limit,
                "cached_quote_count": len(self._stock_ticks),
                "last_stock_quote_time": (
                    datetime.fromtimestamp(latest_ts, TW_TZ).isoformat() if latest_ts else None
                ),
                "stock_quote_age_seconds": age,
                "stock_quote_stale": age is not None and age > self._stale_seconds,
                "active_codes": list(self._stock_subscriptions.keys()),
                "errors": dict(self._stock_errors),
            }

    def get_active_stock_codes(self) -> list[str]:
        with self._stock_lock:
            return list(self._stock_subscriptions.keys())

    def get_stock_quote(self, stock_code: str) -> Optional[dict[str, Any]]:
        code = str(stock_code).strip().upper()
        with self._stock_lock:
            tick = self._stock_ticks.get(code)
            if tick is None:
                return None
            result = dict(tick)
            timestamp = self._stock_tick_timestamps.get(code)
            age = round(time.time() - timestamp, 1) if timestamp else None
            result["quote_age_seconds"] = age
            result["quote_stale"] = age is not None and age > self._stale_seconds
            result["subscribed"] = code in self._stock_subscriptions
            return result

    def get_stock_quotes(self, stock_codes: Iterable[str]) -> dict[str, Optional[dict[str, Any]]]:
        return {str(code).strip().upper(): self.get_stock_quote(str(code)) for code in stock_codes}

    def ensure_stock_subscriptions(self, stock_codes: Iterable[str]) -> dict[str, Any]:
        """確保指定股票已訂閱 Tick 行情，並以 LRU 控制總訂閱數。"""
        codes = []
        seen: set[str] = set()
        for raw in stock_codes:
            code = str(raw).strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            codes.append(code)

        result: dict[str, Any] = {
            "requested": codes,
            "newly_subscribed": [],
            "already_subscribed": [],
            "evicted": [],
            "failed": {},
            "active_count": 0,
            "capacity": self._stock_subscription_limit,
        }

        if not self.state.logged_in or self.api is None:
            result["failed"] = {code: "Shioaji 尚未登入" for code in codes}
            return result

        requested_set = set(codes)
        for code in codes:
            with self._stock_lock:
                if code in self._stock_subscriptions:
                    self._stock_subscriptions[code] = time.time()
                    self._stock_subscriptions.move_to_end(code)
                    result["already_subscribed"].append(code)
                    continue

            evicted = self._evict_until_capacity(requested_set)
            result["evicted"].extend(evicted)

            with self._stock_lock:
                capacity_full = len(self._stock_subscriptions) >= self._stock_subscription_limit
            if capacity_full:
                result["failed"][code] = "台股即時行情訂閱容量已滿"
                continue

            if self._subscribe_stock(code):
                result["newly_subscribed"].append(code)
            else:
                with self._stock_lock:
                    result["failed"][code] = self._stock_errors.get(code, "訂閱失敗")

        with self._stock_lock:
            result["active_count"] = len(self._stock_subscriptions)
        return result

    # ------------------------------------------------------------------
    # 初始化與登入
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        logger.info("[Shioaji] 初始化中...")
        simulation = os.getenv("SHIOAJI_SIMULATION", "false").lower() == "true"
        self.api = sj.Shioaji(simulation=simulation)
        self._callbacks_api_id = None
        self.state.initialized = True
        logger.info("[Shioaji] 初始化成功（%s）。", "模擬模式" if simulation else "正式模式")

    def _login(self) -> None:
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
            self.api.login(api_key=api_key, secret_key=secret_key)
            self.state.logged_in = True
            self.state.error_message = None
            logger.info("[Shioaji] 登入成功。")
        except Exception as exc:
            logger.error("[Shioaji] 登入失敗: %s", exc)
            self.state.error_message = f"登入失敗: {exc}"
            self.state.logged_in = False

    def _activate_ca(self) -> None:
        if not self.state.logged_in or self.api is None:
            return

        ca_path = os.getenv("SHIOAJI_CA_PATH", "")
        ca_passwd = os.getenv("SHIOAJI_CA_PASSWD", "")
        person_id = os.getenv("SHIOAJI_PERSON_ID", "")
        if not ca_path or not ca_passwd or not person_id:
            logger.info("[Shioaji] 未設定憑證相關環境變數，跳過憑證啟用。")
            return

        try:
            result = self.api.activate_ca(
                ca_path=ca_path,
                ca_passwd=ca_passwd,
                person_id=person_id,
            )
            self.state.certificate_active = bool(result)
            if result:
                logger.info("[Shioaji] 電子憑證啟用成功。")
            else:
                logger.warning("[Shioaji] 電子憑證啟用回傳 False。")
        except Exception as exc:
            logger.error("[Shioaji] 電子憑證啟用失敗: %s", exc)
            self.state.error_message = f"憑證啟用失敗: {exc}"

    # ------------------------------------------------------------------
    # 回呼與資料轉換
    # ------------------------------------------------------------------

    def _setup_callbacks(self) -> None:
        if self.api is None or self._callbacks_api_id == id(self.api):
            return

        @self.api.quote.on_event
        def _event_callback(resp_code: int, event_code: int, info: str, event: str):
            event_str = f"code={event_code}, resp={resp_code}, info={info}, event={event}"
            self.state.set_event(event_str)
            logger.info("[Shioaji][Event] %s", event_str)
            info_upper = str(info).upper()

            if event_code == 0:
                self.state.quote_connected = True
            elif event_code in (1, 2):
                self.state.quote_connected = False
                self.state.subscribed = False
                self._trigger_reconnect()
            elif event_code == 12:
                self.state.quote_connected = False
                self.state.subscribed = False
                self.state.reconnect_count += 1
            elif event_code == 13:
                self.state.quote_connected = True
                self._do_subscribe_futures()
                self._resubscribe_stocks()
            elif event_code == 16:
                # 只把期貨訂閱確認寫入 futures subscribed；股票另由 active set 管理。
                futures_markers = ("FOP", self._target_code.upper())
                if self._resolved_futures_code:
                    futures_markers += (self._resolved_futures_code.upper(),)
                if any(marker and marker in info_upper for marker in futures_markers):
                    self.state.subscribed = True

        @self.api.on_tick_fop_v1()
        def _futures_tick_callback(exchange: sj.Exchange, tick: sj.TickFOPv1):
            now = datetime.now(TW_TZ).isoformat()
            tick_time = _format_tick_datetime(getattr(tick, "datetime", None), now)
            tick_data = {
                "code": str(getattr(tick, "code", "")),
                "close": _safe_float(getattr(tick, "close", None)),
                "volume": _safe_int(getattr(tick, "volume", None)),
                "total_volume": _safe_int(getattr(tick, "total_volume", None)),
                "tick_type": _safe_int(getattr(tick, "tick_type", None)),
                "high": _safe_float(getattr(tick, "high", None)),
                "low": _safe_float(getattr(tick, "low", None)),
                "open": _safe_float(getattr(tick, "open", None)),
                "price_chg": _safe_float(getattr(tick, "price_chg", None)),
                "pct_chg": _safe_float(getattr(tick, "pct_chg", None)),
                "bid_side_total_vol": _safe_int(getattr(tick, "bid_side_total_vol", None)),
                "ask_side_total_vol": _safe_int(getattr(tick, "ask_side_total_vol", None)),
                "simtrade": bool(getattr(tick, "simtrade", False)),
                "tick_time": tick_time,
                "received_at": now,
            }
            self.state.update_tick(tick_time, tick_data)

        @self.api.on_tick_stk_v1()
        def _stock_tick_callback(exchange: sj.Exchange, tick: sj.TickSTKv1):
            tick_data = self._stock_tick_to_dict(exchange, tick)
            code = tick_data["code"]
            with self._stock_lock:
                self._stock_ticks[code] = tick_data
                self._stock_tick_timestamps[code] = time.time()
                self._stock_errors.pop(code, None)
                if code in self._stock_subscriptions:
                    self._stock_subscriptions[code] = time.time()
            self.state.quote_connected = True

        self._callbacks_api_id = id(self.api)

    @staticmethod
    def _stock_tick_to_dict(exchange: Any, tick: Any) -> dict[str, Any]:
        now = datetime.now(TW_TZ).isoformat()
        tick_time = _format_tick_datetime(getattr(tick, "datetime", None), now)
        raw_pct = _safe_float(getattr(tick, "pct_chg", None))
        # Shioaji TickSTKv1 的 pct_chg 為百分比的 1/100（例如 33 = 0.33%）。
        pct_chg = round(raw_pct / 100.0, 4) if raw_pct is not None else None
        exchange_value = getattr(exchange, "value", None) or str(exchange).split(".")[-1]

        return {
            "code": str(getattr(tick, "code", "")).upper(),
            "exchange": str(exchange_value),
            "close": _safe_float(getattr(tick, "close", None)),
            "open": _safe_float(getattr(tick, "open", None)),
            "high": _safe_float(getattr(tick, "high", None)),
            "low": _safe_float(getattr(tick, "low", None)),
            "avg_price": _safe_float(getattr(tick, "avg_price", None)),
            "price_chg": _safe_float(getattr(tick, "price_chg", None)),
            "pct_chg": pct_chg,
            "volume": _safe_int(getattr(tick, "volume", None)),
            "total_volume": _safe_int(getattr(tick, "total_volume", None)),
            "amount": _safe_float(getattr(tick, "amount", None)),
            "total_amount": _safe_float(getattr(tick, "total_amount", None)),
            "tick_type": _safe_int(getattr(tick, "tick_type", None)),
            "chg_type": _safe_int(getattr(tick, "chg_type", None)),
            "bid_side_total_vol": _safe_int(getattr(tick, "bid_side_total_vol", None)),
            "ask_side_total_vol": _safe_int(getattr(tick, "ask_side_total_vol", None)),
            "bid_side_total_cnt": _safe_int(getattr(tick, "bid_side_total_cnt", None)),
            "ask_side_total_cnt": _safe_int(getattr(tick, "ask_side_total_cnt", None)),
            "suspend": bool(getattr(tick, "suspend", False)),
            "simtrade": bool(getattr(tick, "simtrade", False)),
            "intraday_odd": bool(getattr(tick, "intraday_odd", False)),
            "tick_time": tick_time,
            "received_at": now,
            "data_source": "shioaji_realtime_stock",
        }

    # ------------------------------------------------------------------
    # 訂閱管理
    # ------------------------------------------------------------------

    def _do_subscribe_futures(self) -> None:
        if not self.state.logged_in or self.api is None:
            return

        logger.info("[Shioaji] 訂閱台指期行情: %s", self._target_code)
        try:
            contract = self.api.contracts.get(self._target_code)
            if contract is None:
                # 兼容 legacy Contracts 存取方式
                contract = self.api.Contracts.Futures.TXF[self._target_code]
            if contract is None:
                raise ValueError(f"找不到合約: {self._target_code}")

            self._resolved_futures_code = (
                getattr(contract, "target_code", None) or getattr(contract, "code", None)
            )
            self.state.current_contract = self._target_code
            self.state.data_source = f"shioaji_realtime_{self._target_code}"
            self.api.subscribe(contract, quote_type=sj.QuoteType.Tick)
            logger.info("[Shioaji] 台指期 subscribe() 已呼叫，等待 Event/Tick 確認。")
        except Exception as exc:
            logger.error("[Shioaji] 台指期訂閱失敗: %s", exc)
            self.state.error_message = f"台指期訂閱失敗: {exc}"
            self.state.subscribed = False

    def _subscribe_bootstrap_stocks(self) -> None:
        raw = os.getenv("SHIOAJI_STOCK_BOOTSTRAP_CODES", "")
        codes = [item.strip() for item in raw.split(",") if item.strip()]
        if codes:
            self.ensure_stock_subscriptions(codes)

    def _resolve_stock_contract(self, code: str) -> Any:
        if self.api is None:
            return None
        contract = self.api.contracts.get(code)
        if contract is None:
            try:
                contract = self.api.Contracts.Stocks[code]
            except Exception:
                contract = None
        if contract is None:
            return None

        security_type = str(getattr(contract, "security_type", "")).upper()
        if security_type and "STK" not in security_type and "STOCK" not in security_type:
            return None
        return contract

    def _subscribe_stock(self, code: str) -> bool:
        if self.api is None:
            return False
        try:
            contract = self._resolve_stock_contract(code)
            if contract is None:
                raise ValueError(f"找不到股票合約：{code}")
            self.api.subscribe(contract, quote_type=sj.QuoteType.Tick)
            with self._stock_lock:
                self._stock_contracts[code] = contract
                self._stock_subscriptions[code] = time.time()
                self._stock_subscriptions.move_to_end(code)
                self._stock_errors.pop(code, None)
            logger.info("[Shioaji] 已請求訂閱台股 Tick: %s", code)
            return True
        except Exception as exc:
            message = str(exc)
            with self._stock_lock:
                self._stock_errors[code] = message
            logger.warning("[Shioaji] 台股 %s 訂閱失敗: %s", code, exc)
            return False

    def _unsubscribe_stock(self, code: str) -> None:
        contract = None
        with self._stock_lock:
            contract = self._stock_contracts.pop(code, None)
            self._stock_subscriptions.pop(code, None)
        if self.api is not None and contract is not None:
            try:
                self.api.unsubscribe(contract, quote_type=sj.QuoteType.Tick)
                logger.info("[Shioaji] 已取消台股 Tick 訂閱: %s", code)
            except Exception as exc:
                logger.warning("[Shioaji] 取消 %s 訂閱失敗: %s", code, exc)

    def _evict_until_capacity(self, protected_codes: set[str]) -> list[str]:
        evicted: list[str] = []
        while True:
            with self._stock_lock:
                if len(self._stock_subscriptions) < self._stock_subscription_limit:
                    break
                victim = next(
                    (code for code in self._stock_subscriptions if code not in protected_codes),
                    None,
                )
                if victim is None:
                    break
            self._unsubscribe_stock(victim)
            evicted.append(victim)
        return evicted

    def _resubscribe_stocks(self) -> None:
        with self._stock_lock:
            codes = list(self._stock_subscriptions.keys())
            self._stock_subscriptions.clear()
            self._stock_contracts.clear()
        for code in codes:
            self._subscribe_stock(code)

    # ------------------------------------------------------------------
    # 斷線重連
    # ------------------------------------------------------------------

    def _trigger_reconnect(self) -> None:
        if self._shutdown_event.is_set():
            return
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            name="shioaji-reconnect",
            daemon=True,
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        attempt = 0
        while attempt < MAX_RECONNECT_ATTEMPTS and not self._shutdown_event.is_set():
            attempt += 1
            interval = min(
                RECONNECT_BASE_INTERVAL * (2 ** (attempt - 1)),
                RECONNECT_MAX_INTERVAL,
            )
            logger.info(
                "[Shioaji] 重連嘗試 %d/%d，等待 %d 秒...",
                attempt,
                MAX_RECONNECT_ATTEMPTS,
                interval,
            )
            if self._shutdown_event.wait(timeout=interval):
                return
            if self.state.quote_connected:
                return

            try:
                active_codes = self.get_active_stock_codes()
                if self.api:
                    try:
                        self.api.logout()
                    except Exception:
                        pass

                self.state.logged_in = False
                self.state.quote_connected = False
                self.state.subscribed = False
                with self._stock_lock:
                    self._stock_subscriptions = OrderedDict((code, time.time()) for code in active_codes)
                    self._stock_contracts.clear()

                self._initialize()
                self._login()
                if not self.state.logged_in:
                    continue
                self._activate_ca()
                self._setup_callbacks()
                self._do_subscribe_futures()
                self._resubscribe_stocks()

                time.sleep(10)
                if self.state.quote_connected or self.state.subscribed:
                    return
            except Exception as exc:
                logger.error("[Shioaji] 重連嘗試 %d 失敗: %s", attempt, exc)
                self.state.error_message = (
                    f"重連失敗 ({attempt}/{MAX_RECONNECT_ATTEMPTS}): {exc}"
                )

        if attempt >= MAX_RECONNECT_ATTEMPTS:
            self.state.error_message = f"已達重連上限 ({MAX_RECONNECT_ATTEMPTS} 次)，停止重連。"


_service: Optional[QuoteService] = None


def get_quote_service() -> QuoteService:
    """取得全域 QuoteService 單例。"""
    global _service
    if _service is None:
        _service = QuoteService()
    return _service
