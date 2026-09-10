"""HanStock 即時行情服務模組。

負責 Shioaji 登入、台股即時行情訂閱、行情快取，以及斷線重連邏輯。
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

from market_data_hub import get_market_data_hub

logger = logging.getLogger("hanstock.quote_service")
TW_TZ = timezone(timedelta(hours=8))
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_BASE_INTERVAL = 5
RECONNECT_MAX_INTERVAL = 300
DEFAULT_STALE_SECONDS = 60.0
DEFAULT_STOCK_SUBSCRIPTION_LIMIT = 190
DEFAULT_RAILWAY_STARTUP_DELAY_SECONDS = 15.0
DEFAULT_PRIMARY_RAILWAY_PROJECT_ID = "4b2403bb-cd2d-4917-bd8f-80dffe894d00"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try: value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError): value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try: value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError): value = default
    return max(minimum, min(maximum, value))


def quote_deployment_role() -> str:
    current = os.getenv("RAILWAY_PROJECT_ID", "").strip()
    primary = os.getenv("HANSTOCK_PRIMARY_RAILWAY_PROJECT_ID", DEFAULT_PRIMARY_RAILWAY_PROJECT_ID).strip()
    return "standby" if current and primary and current != primary else "primary"


def quote_startup_delay_seconds() -> float:
    if quote_deployment_role() != "primary" or not os.getenv("RAILWAY_PROJECT_ID", "").strip(): return 0.0
    return _env_float("SHIOAJI_RAILWAY_STARTUP_DELAY_SECONDS", DEFAULT_RAILWAY_STARTUP_DELAY_SECONDS, 5.0, 120.0)


def _safe_float(value: Any) -> Optional[float]:
    if value is None: return None
    try: return float(value)
    except (TypeError, ValueError): return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None: return None
    try: return int(value)
    except (TypeError, ValueError): return None


def _format_tick_datetime(value: Any, fallback: str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None: value = value.replace(tzinfo=TW_TZ)
        return value.astimezone(TW_TZ).isoformat()
    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            us = int(value[6]) if len(value) > 6 else 0
            return datetime(int(value[0]), int(value[1]), int(value[2]), int(value[3]), int(value[4]), int(value[5]), us, tzinfo=TW_TZ).isoformat()
        except (TypeError, ValueError): pass
    return str(value) if value else fallback


@dataclass
class QuoteState:
    initialized: bool = False
    logged_in: bool = False
    certificate_active: bool = False
    quote_connected: bool = False
    subscribed: bool = False
    last_quote_time: Optional[str] = None
    last_quote_timestamp: Optional[float] = None
    last_tick_data: Optional[dict[str, Any]] = None
    last_event: Optional[str] = None
    error_message: Optional[str] = None
    data_source: str = "none"
    reconnect_count: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def to_dict(self, stale_seconds: float = DEFAULT_STALE_SECONDS) -> dict[str, Any]:
        with self._lock:
            age = round(time.time() - self.last_quote_timestamp, 1) if self.last_quote_timestamp is not None else None
            return {"shioaji_initialized": self.initialized, "shioaji_logged_in": self.logged_in, "certificate_active": self.certificate_active, "quote_connected": self.quote_connected, "subscribed": self.subscribed, "last_quote_time": self.last_quote_time, "quote_age_seconds": age, "quote_stale": age is not None and age > stale_seconds, "current_contract": None, "last_event": self.last_event, "data_source": self.data_source, "reconnect_count": self.reconnect_count, "error_message": self.error_message}

    def update_tick(self, tick_time: str, tick_data: dict[str, Any]) -> None:
        with self._lock:
            self.last_quote_time = tick_time; self.last_quote_timestamp = time.time(); self.last_tick_data = dict(tick_data); self.quote_connected = True; self.subscribed = True

    def set_event(self, event_str: str) -> None:
        with self._lock: self.last_event = event_str


class QuoteService:
    """Shioaji 即時行情長駐服務，只處理台股。"""

    def __init__(self) -> None:
        self.api: Optional[sj.Shioaji] = None
        self.state = QuoteState()
        self._shutdown_event = threading.Event()
        self._reconnect_thread: Optional[threading.Thread] = None
        self._reconnect_lock = threading.RLock()
        self._startup_lock = threading.Lock()
        self._last_p2p_recovery_at = float("-inf")
        self._stale_seconds = _env_float("SHIOAJI_QUOTE_STALE_SECONDS", DEFAULT_STALE_SECONDS, 5.0, 3600.0)
        self._callbacks_api_id: Optional[int] = None
        self._stock_lock = threading.RLock()
        self._stock_main_subscription_lock = threading.Lock()
        self._stock_ticks: dict[str, dict[str, Any]] = {}
        self._stock_tick_timestamps: dict[str, float] = {}
        self._stock_contracts: dict[str, Any] = {}
        self._stock_subscriptions: OrderedDict[str, float] = OrderedDict()
        self._stock_assignments: dict[str, str] = {}
        self._stock_errors: dict[str, str] = {}
        legacy = _env_int("SHIOAJI_STOCK_MAX_SUBSCRIPTIONS", DEFAULT_STOCK_SUBSCRIPTION_LIMIT, 1, 190)
        self._stock_subscription_limit = _env_int("SHIOAJI_MAIN_STOCK_MAX_SUBSCRIPTIONS", max(DEFAULT_STOCK_SUBSCRIPTION_LIMIT, legacy), 1, 190)

    def startup(self) -> None:
        with self._startup_lock:
            if self.state.logged_in or self._shutdown_event.is_set(): return
            self._startup_once()

    def _startup_once(self) -> None:
        if quote_deployment_role() != "primary":
            self.state.data_source = "standby_no_shioaji_login"; logger.info("[Shioaji] Railway 備援專案不登入行情。"); return
        try:
            self._initialize(); self._login()
            if not self.state.logged_in: logger.warning("[Shioaji] 啟動登入未成功，交由背景重連流程恢復。"); self._trigger_reconnect(); return
            self._activate_ca(); self._setup_callbacks(); self._subscribe_bootstrap_stocks()
        except Exception as exc:
            logger.error("即時行情啟動流程發生未預期錯誤: %s", exc); self.state.error_message = str(exc)

    def shutdown(self) -> None:
        self._shutdown_event.set()
        if self._reconnect_thread and self._reconnect_thread.is_alive(): self._reconnect_thread.join(timeout=5)
        if self.api and self.state.logged_in:
            try: self.api.logout(); logger.info("Shioaji 已安全登出。")
            except Exception as exc: logger.warning("登出時發生錯誤: %s", exc)
        self.state.logged_in = False; self.state.quote_connected = False; self.state.subscribed = False

    def get_health(self) -> dict[str, Any]:
        health = self.state.to_dict(self._stale_seconds); health["quote_role"] = quote_deployment_role(); health["futures_enabled"] = False; return health

    def get_latest_tick(self) -> Optional[dict[str, Any]]:
        with self.state._lock: return dict(self.state.last_tick_data) if self.state.last_tick_data else None

    def recover_transient_p2p_session(self, reason: str = "P2P SessionNotEstablished") -> bool:
        if quote_deployment_role() != "primary": return False
        with self._reconnect_lock:
            now = time.monotonic()
            if now - self._last_p2p_recovery_at < 60.0: return False
            with self.state._lock:
                last_tick = self.state.last_quote_timestamp
                if self.state.logged_in and last_tick is not None and 0 <= time.time() - last_tick <= 120.0: return False
                self.state.quote_connected = False; self.state.subscribed = False; self.state.error_message = reason
            self._last_p2p_recovery_at = now; self._trigger_reconnect(); return True

    def get_stock_health(self) -> dict[str, Any]:
        with self._stock_lock:
            ts = list(self._stock_tick_timestamps.values()); latest = max(ts) if ts else None; age = round(time.time() - latest, 1) if latest else None
            main_count = sum(1 for v in self._stock_assignments.values() if v == "main")
            return {"enabled": self.state.logged_in, "active_subscription_count": len(self._stock_subscriptions), "subscription_limit": self._stock_subscription_limit, "main_connection_limit": self._stock_subscription_limit, "main_connection_active_count": main_count, "shared_pool_active_count": 0, "shared_pool_capacity": 0, "eviction_policy": "disabled", "cached_quote_count": len(self._stock_ticks), "last_stock_quote_time": datetime.fromtimestamp(latest, TW_TZ).isoformat() if latest else None, "stock_quote_age_seconds": age, "stock_quote_stale": age is not None and age > self._stale_seconds, "active_codes": list(self._stock_subscriptions.keys()), "errors": dict(self._stock_errors)}

    def get_active_stock_codes(self) -> list[str]:
        with self._stock_lock: return list(self._stock_subscriptions.keys())

    def get_stock_quote(self, stock_code: str) -> Optional[dict[str, Any]]:
        code = str(stock_code).strip().upper()
        with self._stock_lock:
            tick = self._stock_ticks.get(code)
            if tick is None: return None
            result = dict(tick); timestamp = self._stock_tick_timestamps.get(code); age = round(time.time() - timestamp, 1) if timestamp else None
            result["quote_age_seconds"] = age; result["quote_stale"] = age is not None and age > self._stale_seconds; result["subscribed"] = code in self._stock_subscriptions; return result

    def get_stock_quotes(self, stock_codes: Iterable[str]) -> dict[str, Optional[dict[str, Any]]]: return {str(code).strip().upper(): self.get_stock_quote(str(code)) for code in stock_codes}

    def ensure_stock_subscriptions(self, stock_codes: Iterable[str]) -> dict[str, Any]:
        codes=[]; seen=set()
        for raw in stock_codes:
            code=str(raw).strip().upper()
            if code and code not in seen: seen.add(code); codes.append(code)
        result={"requested":codes,"newly_subscribed":[],"already_subscribed":[],"evicted":[],"failed":{},"active_count":0,"capacity":self._stock_subscription_limit,"main_capacity":self._stock_subscription_limit,"shared_capacity":0}
        if not self.state.logged_in or self.api is None:
            result["failed"]={code:"Shioaji 尚未登入" for code in codes}; return result
        for code in codes:
            with self._stock_lock:
                if code in self._stock_subscriptions:
                    self._stock_subscriptions[code]=time.time(); self._stock_subscriptions.move_to_end(code); result["already_subscribed"].append(code); continue
                main_count=sum(1 for v in self._stock_assignments.values() if v=="main")
            if main_count >= self._stock_subscription_limit:
                result["failed"][code]="台股訂閱上限"; continue
            if self._subscribe_stock(code): result["newly_subscribed"].append(code)
            else:
                with self._stock_lock: result["failed"][code]=self._stock_errors.get(code,"訂閱失敗")
        with self._stock_lock: result["active_count"]=len(self._stock_subscriptions)
        return result

    def _initialize(self) -> None:
        simulation=os.getenv("SHIOAJI_SIMULATION","false").lower()=="true"; self.api=sj.Shioaji(simulation=simulation); self._callbacks_api_id=None; self.state.initialized=True; logger.info("[Shioaji] 初始化成功（%s）。", "模擬模式" if simulation else "正式模式")

    def _login(self) -> None:
        if not self.state.initialized or self.api is None: return
        api_key=os.getenv("SHIOAJI_API_KEY",""); secret_key=os.getenv("SHIOAJI_SECRET_KEY","")
        if not api_key or not secret_key: self.state.error_message="缺少 SHIOAJI_API_KEY 或 SHIOAJI_SECRET_KEY 環境變數。"; logger.error("[Shioaji] %s",self.state.error_message); return
        try:
            self.api.login(api_key=api_key,secret_key=secret_key,subscribe_trade=False); self.state.logged_in=True; self.state.error_message=None; logger.info("[Shioaji] 登入成功。")
        except Exception as exc: self.state.error_message=f"登入失敗: {exc}"; self.state.logged_in=False; logger.error("[Shioaji] 登入失敗: %s",exc)

    def _activate_ca(self) -> None:
        if not self.state.logged_in or self.api is None: return
        ca_path=os.getenv("SHIOAJI_CA_PATH",""); ca_passwd=os.getenv("SHIOAJI_CA_PASSWD",""); person_id=os.getenv("SHIOAJI_PERSON_ID","")
        if not ca_path or not ca_passwd or not person_id: return
        try:
            result=self.api.activate_ca(ca_path=ca_path,ca_passwd=ca_passwd,person_id=person_id); self.state.certificate_active=bool(result)
        except Exception as exc: logger.error("[Shioaji] 憑證啟用失敗: %s",exc)

    def _setup_callbacks(self) -> None:
        if self.api is None: return
        if self._callbacks_api_id == id(self.api): return
        @self.api.on_tick_stk_v1()
        def _stock_tick_callback(exchange: sj.Exchange, tick: sj.TickSTKv1): self._handle_stock_tick(exchange,tick,primary_connection=True)
        self._callbacks_api_id=id(self.api)

    @staticmethod
    def _stock_tick_to_dict(exchange: Any, tick: Any) -> dict[str, Any]:
        now=datetime.now(TW_TZ).isoformat(); raw_pct=_safe_float(getattr(tick,"pct_chg",None)); pct_chg=round(raw_pct/100.0,4) if raw_pct is not None else None; exchange_value=getattr(exchange,"value",None) or str(exchange).split(".")[-1]
        return {"code":str(getattr(tick,"code","")).upper(),"exchange":str(exchange_value),"close":_safe_float(getattr(tick,"close",None)),"open":_safe_float(getattr(tick,"open",None)),"high":_safe_float(getattr(tick,"high",None)),"low":_safe_float(getattr(tick,"low",None)),"avg_price":_safe_float(getattr(tick,"avg_price",None)),"price_chg":_safe_float(getattr(tick,"price_chg",None)),"pct_chg":pct_chg,"volume":_safe_int(getattr(tick,"volume",None)),"total_volume":_safe_int(getattr(tick,"total_volume",None)),"amount":_safe_float(getattr(tick,"amount",None)),"total_amount":_safe_float(getattr(tick,"total_amount",None)),"tick_type":_safe_int(getattr(tick,"tick_type",None)),"chg_type":_safe_int(getattr(tick,"chg_type",None)),"bid_side_total_vol":_safe_int(getattr(tick,"bid_side_total_vol",None)),"ask_side_total_vol":_safe_int(getattr(tick,"ask_side_total_vol",None)),"bid_side_total_cnt":_safe_int(getattr(tick,"bid_side_total_cnt",None)),"ask_side_total_cnt":_safe_int(getattr(tick,"ask_side_total_cnt",None)),"suspend":bool(getattr(tick,"suspend",False)),"simtrade":bool(getattr(tick,"simtrade",False)),"intraday_odd":bool(getattr(tick,"intraday_odd",False)),"tick_time":_format_tick_datetime(getattr(tick,"datetime",None),now),"received_at":now,"data_source":"shioaji_realtime_stock"}

    def _handle_stock_tick(self, exchange: Any, tick: Any, *, primary_connection: bool=False) -> None:
        data=self._stock_tick_to_dict(exchange,tick); code=data["code"]
        if not code: return
        if primary_connection:
            with self.state._lock: self.state.last_quote_timestamp=time.time(); self.state.quote_connected=True
        with self._stock_lock:
            self._stock_ticks[code]=data; self._stock_tick_timestamps[code]=time.time(); self._stock_errors.pop(code,None)
            if code in self._stock_subscriptions: self._stock_subscriptions[code]=time.time()
        try: get_market_data_hub().on_stock_tick(data)
        except Exception as exc: logger.debug("[Hub] stock tick 推送失敗: %s",exc)

    def _subscribe_bootstrap_stocks(self) -> None:
        raw=os.getenv("SHIOAJI_STOCK_BOOTSTRAP_CODES",""); codes=[x.strip() for x in raw.split(",") if x.strip()]
        if codes: self.ensure_stock_subscriptions(codes)

    def _resolve_stock_contract(self, code: str) -> Any:
        if self.api is None: return None
        contract=self.api.contracts.get(code)
        if contract is None:
            try: contract=self.api.Contracts.Stocks[code]
            except Exception: contract=None
        if contract is None: return None
        security_type=str(getattr(contract,"security_type","")).upper()
        if security_type and "STK" not in security_type and "STOCK" not in security_type: return None
        return contract

    def _subscribe_stock(self, code: str) -> bool:
        if self.api is None: return False
        try:
            contract=self._resolve_stock_contract(code)
            if contract is None: raise ValueError(f"找不到股票合約：{code}")
            self.api.subscribe(contract,quote_type=sj.QuoteType.Tick)
            with self._stock_lock:
                self._stock_contracts[code]=contract; self._stock_subscriptions[code]=time.time(); self._stock_subscriptions.move_to_end(code); self._stock_assignments[code]="main"; self._stock_errors.pop(code,None)
            return True
        except Exception as exc:
            with self._stock_lock: self._stock_errors[code]=str(exc)
            logger.warning("[Shioaji] 台股 %s 訂閱失敗: %s",code,exc); return False

    def _unsubscribe_stock(self, code: str) -> None:
        with self._stock_lock: contract=self._stock_contracts.pop(code,None); assignment=self._stock_assignments.pop(code,None); self._stock_subscriptions.pop(code,None)
        if assignment=="main" and self.api is not None and contract is not None:
            try: self.api.unsubscribe(contract,quote_type=sj.QuoteType.Tick)
            except Exception as exc: logger.warning("[Shioaji] 取消 %s 訂閱失敗: %s",code,exc)

    def _resubscribe_stocks(self) -> None:
        with self._stock_lock:
            codes=[code for code,assignment in self._stock_assignments.items() if assignment=="main"]
            for code in codes: self._stock_contracts.pop(code,None)
        for code in codes: self._subscribe_stock(code)

    def _trigger_reconnect(self) -> None:
        with self._reconnect_lock:
            if self._shutdown_event.is_set() or quote_deployment_role()!="primary": return
            if self._reconnect_thread and self._reconnect_thread.is_alive(): return
            self._reconnect_thread=threading.Thread(target=self._reconnect_serialized,name="shioaji-reconnect",daemon=True); self._reconnect_thread.start()

    def _reconnect_serialized(self) -> None:
        with self._startup_lock: self._reconnect_loop()

    def _reconnect_loop(self) -> None:
        attempt=0
        while attempt<MAX_RECONNECT_ATTEMPTS and not self._shutdown_event.is_set():
            attempt+=1
            with self.state._lock: self.state.reconnect_count+=1
            interval=min(RECONNECT_BASE_INTERVAL*(2**(attempt-1)),RECONNECT_MAX_INTERVAL)
            if "503" in str(self.state.error_message) or "1分鐘" in str(self.state.error_message): interval=max(interval,60)
            if self._shutdown_event.wait(timeout=interval): return
            if self.state.quote_connected: return
            try:
                if self.api:
                    try: self.api.logout()
                    except Exception: pass
                self.state.logged_in=False; self.state.quote_connected=False; self.state.subscribed=False
                with self._stock_lock:
                    for code,assignment in list(self._stock_assignments.items()):
                        if assignment=="main": self._stock_contracts.pop(code,None)
                self._initialize(); self._login()
                if not self.state.logged_in: continue
                self._activate_ca(); self._setup_callbacks(); self._resubscribe_stocks(); time.sleep(10)
                if self.state.quote_connected or self.state.subscribed: return
            except Exception as exc:
                self.state.error_message=f"重連失敗 ({attempt}/{MAX_RECONNECT_ATTEMPTS}): {exc}"; logger.error("[Shioaji] 重連嘗試 %d 失敗: %s",attempt,exc)
        if attempt>=MAX_RECONNECT_ATTEMPTS: self.state.error_message=f"已達重連上限 ({MAX_RECONNECT_ATTEMPTS} 次)，停止重連。"


_service: Optional[QuoteService] = None

def get_quote_service() -> QuoteService:
    global _service
    if _service is None: _service=QuoteService()
    return _service
