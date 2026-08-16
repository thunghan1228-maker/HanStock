"""HanStock 股票期貨行情服務。

設計原則
- 股票期貨不使用現貨行情。
- 盤中 08:45～13:45 使用 Shioaji FOP Quote 訂閱，不用 snapshots 輪詢冒充即時。
- 休市時允許以 Shioaji Futures Snapshot 補最近交易日近月收盤，供週末/盤後查看。
- 使用 Shioaji FOP Quote 訂閱；每一條連線都遠低於官方 200 訂閱上限。
- 預設建立 4 條共享 Shioaji 行情連線，與既有台指期主連線合計 5 條，
  嚴格遵守同一 person_id 最多 5 條連線的官方限制。
- 共享連線同時承載全市場現貨 Tick 與股票期貨 Quote；每條保留安全餘裕，
  避免為全股掃描另開連線而擠掉股票期貨功能。
- 每檔依股票標的反查 R1 近月連續月；不寫死交割月份。
- 定期重查 R1 target_code；換月時自動取消舊 target 並訂閱新 target。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable, Iterable, Literal, Optional

import shioaji as sj

logger = logging.getLogger("hanstock.stock_futures")
TW_TZ = timezone(timedelta(hours=8))
StockFuturesMode = Literal["regular", "mini"]
DEFAULT_POOL_SIZE = 4
DEFAULT_PER_CONNECTION_CAP = 195
DEFAULT_CONTRACT_READY_TIMEOUT_SECONDS = 20.0
DEFAULT_FRONT_MONTH_RECHECK_SECONDS = 300.0
DEFAULT_CLOSED_SNAPSHOT_RECHECK_SECONDS = 600.0
DEFAULT_POOL_RECOVERY_DELAY_SECONDS = 35.0
DEFAULT_POOL_RECOVERY_COOLDOWN_SECONDS = 120.0
TRANSIENT_SESSION_MARKERS = (
    "SESSIONNOTESTABLISHED",
    "SESSION ERROR",
    "NOTREADY",
    "TOKEN IS EXPIRED",
    "TRANSIENT TRANSPORT FAILURE",
)

# 與 quote_service 共用相同預設值，但不反向匯入 quote_service，避免測試隔離載入及循環匯入。
DEFAULT_PRIMARY_RAILWAY_PROJECT_ID = "4b2403bb-cd2d-4917-bd8f-80dffe894d00"


def _quote_deployment_role() -> str:
    current_project = os.getenv("RAILWAY_PROJECT_ID", "").strip()
    primary_project = os.getenv(
        "HANSTOCK_PRIMARY_RAILWAY_PROJECT_ID",
        DEFAULT_PRIMARY_RAILWAY_PROJECT_ID,
    ).strip()
    if current_project and primary_project and current_project != primary_project:
        return "standby"
    return "primary"


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
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return 0 if value is None else max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _normalize_codes(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        code = str(raw).strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _quote_datetime(value: Any, quote: Any = None) -> datetime:
    if isinstance(value, datetime):
        return (value if value.tzinfo is not None else value.replace(tzinfo=TW_TZ)).astimezone(TW_TZ)
    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            microsecond = int(value[6]) if len(value) > 6 else 0
            return datetime(
                int(value[0]), int(value[1]), int(value[2]),
                int(value[3]), int(value[4]), int(value[5]), microsecond,
                tzinfo=TW_TZ,
            )
        except (TypeError, ValueError):
            pass
    if quote is not None:
        raw_date = getattr(quote, "date", None)
        raw_time = getattr(quote, "time", None)
        try:
            if hasattr(raw_date, "year") and hasattr(raw_time, "hour"):
                return datetime.combine(raw_date, raw_time, tzinfo=TW_TZ)
        except Exception:
            pass
    return datetime.now(TW_TZ)


def _snapshot_datetime(snapshot: Any) -> datetime:
    raw = getattr(snapshot, "ts", None)
    try:
        ts = float(raw)
        # Shioaji Snapshot ts 通常為 nanoseconds；同時兼容 ms / seconds。
        if ts > 1e16:
            ts /= 1e9
        elif ts > 1e12:
            ts /= 1e3
        if ts > 0:
            return datetime.fromtimestamp(ts, TW_TZ)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return datetime.now(TW_TZ)


def is_stock_futures_day_session(now: Optional[datetime] = None) -> bool:
    current = (now or datetime.now(TW_TZ)).astimezone(TW_TZ)
    if current.weekday() >= 5:
        return False
    clock = current.time().replace(tzinfo=None)
    return dt_time(8, 45) <= clock <= dt_time(13, 45)


def _contract_code(contract: Any) -> str:
    return str(getattr(contract, "code", "") or "").strip().upper()


def _target_code(contract: Any) -> str:
    return str(getattr(contract, "target_code", "") or _contract_code(contract)).strip().upper()


def _is_mini_contract(contract: Any) -> bool:
    """小型股票期貨以名稱或 100 股契約大小辨識，避免個別商品名稱差異。"""
    name = str(getattr(contract, "name", "") or "")
    if "小型" in name:
        return True
    for attr in ("contract_size", "multiplier"):
        value = _safe_float(getattr(contract, attr, None))
        if value is not None and abs(value - 100.0) < 1e-9:
            return True
    return False


def _contract_public(contract: Any) -> dict[str, Any]:
    return {
        "futures_code": _contract_code(contract),
        "target_code": _target_code(contract),
        "name": str(getattr(contract, "name", "") or ""),
        "root": str(getattr(contract, "root", "") or ""),
        "delivery_month": str(getattr(contract, "delivery_month", "") or ""),
        "delivery_date": str(getattr(contract, "delivery_date", "") or ""),
        "underlying_code": str(getattr(contract, "underlying_code", "") or ""),
    }


def _cached_stock_contract(api: Any, code: str) -> Any:
    """優先讀 login 已載入的本機商品檔，避免逐檔觸發遠端 contracts.get。"""
    try:
        return api.Contracts.Stocks[code]
    except Exception:
        return None


def resolve_front_month_contract(api: Any, underlying_code: str, mode: StockFuturesMode) -> Any:
    code = str(underlying_code).strip().upper()
    if mode not in ("regular", "mini"):
        raise ValueError(f"不支援股票期貨模式：{mode}")

    underlying = _cached_stock_contract(api, code)
    if underlying is None:
        try:
            underlying = api.contracts.get(code)
        except Exception:
            underlying = None
    if underlying is None:
        raise ValueError(f"找不到股票標的合約：{code}")

    finder = getattr(api.contracts, "futures_by_underlying", None)
    if not callable(finder):
        raise RuntimeError("目前 Shioaji 不支援 futures_by_underlying")
    futures = list(finder(underlying) or [])

    candidates = []
    for contract in futures:
        ccode = _contract_code(contract)
        if not ccode.endswith("R1"):
            continue
        is_mini = _is_mini_contract(contract)
        if mode == "mini" and not is_mini:
            continue
        if mode == "regular" and is_mini:
            continue
        underlying_value = str(getattr(contract, "underlying_code", "") or code).strip().upper()
        if underlying_value not in ("", code):
            continue
        candidates.append(contract)

    if not candidates:
        label = "小型股票期貨" if mode == "mini" else "股票期貨"
        raise ValueError(f"{code} 找不到{label} R1 近月合約")

    candidates.sort(
        key=lambda c: (
            str(getattr(c, "delivery_month", "") or "999999"),
            _contract_code(c),
        )
    )
    return candidates[0]


@dataclass
class _PoolConnection:
    index: int
    api: Any
    healthy: bool = True
    error: Optional[str] = None
    subscriptions: OrderedDict[tuple[StockFuturesMode, str], float] = field(default_factory=OrderedDict)
    stock_subscriptions: OrderedDict[str, float] = field(default_factory=OrderedDict)
    created_at_monotonic: float = field(default_factory=time.monotonic)


class StockFuturesQuoteService:
    def __init__(self, api_factory: Optional[Callable[[], Any]] = None) -> None:
        self._lock = threading.RLock()
        self._pool_init_lock = threading.Lock()
        self._api_factory = api_factory
        legacy_pool_size = _env_int(
            "SHIOAJI_STOCK_FUTURES_POOL_SIZE", DEFAULT_POOL_SIZE, 2, 4
        )
        self._pool_size = _env_int(
            "SHIOAJI_SHARED_QUOTE_POOL_SIZE",
            max(DEFAULT_POOL_SIZE, legacy_pool_size),
            1,
            4,
        )
        legacy_per_connection_cap = _env_int(
            "SHIOAJI_STOCK_FUTURES_PER_CONNECTION_CAP",
            DEFAULT_PER_CONNECTION_CAP,
            100,
            198,
        )
        self._per_connection_cap = _env_int(
            "SHIOAJI_SHARED_PER_CONNECTION_CAP",
            max(DEFAULT_PER_CONNECTION_CAP, legacy_per_connection_cap),
            100,
            198,
        )
        self._recheck_seconds = _env_float(
            "SHIOAJI_STOCK_FUTURES_R1_RECHECK_SECONDS",
            DEFAULT_FRONT_MONTH_RECHECK_SECONDS,
            30.0,
            3600.0,
        )
        self._closed_snapshot_recheck_seconds = _env_float(
            "SHIOAJI_STOCK_FUTURES_CLOSED_SNAPSHOT_SECONDS",
            DEFAULT_CLOSED_SNAPSHOT_RECHECK_SECONDS,
            60.0,
            3600.0,
        )
        self._contract_ready_timeout = _env_float(
            "SHIOAJI_SHARED_CONTRACT_READY_TIMEOUT_SECONDS",
            DEFAULT_CONTRACT_READY_TIMEOUT_SECONDS,
            3.0,
            60.0,
        )
        self._pool_recovery_delay = _env_float(
            "SHIOAJI_SHARED_POOL_RECOVERY_DELAY_SECONDS",
            DEFAULT_POOL_RECOVERY_DELAY_SECONDS,
            1.0,
            120.0,
        )
        self._pool_recovery_cooldown = _env_float(
            "SHIOAJI_SHARED_POOL_RECOVERY_COOLDOWN_SECONDS",
            DEFAULT_POOL_RECOVERY_COOLDOWN_SECONDS,
            30.0,
            600.0,
        )
        self._pools: list[_PoolConnection] = []
        self._pool_recovery_thread: Optional[threading.Thread] = None
        self._last_pool_recovery_at = 0.0
        self._last_subscription_success_at = 0.0
        self._stock_tick_handler: Optional[Callable[[Any, Any], None]] = None
        self._stock_assignments: dict[str, int] = {}
        self._stock_contracts: dict[str, Any] = {}
        self._stock_errors: dict[str, str] = {}
        self._assignments: dict[tuple[StockFuturesMode, str], int] = {}
        self._contracts: dict[tuple[StockFuturesMode, str], Any] = {}
        self._targets: dict[tuple[StockFuturesMode, str], str] = {}
        self._last_resolved_at: dict[tuple[StockFuturesMode, str], float] = {}
        self._reverse_codes: dict[tuple[int, str], tuple[StockFuturesMode, str]] = {}
        self._quotes: dict[tuple[StockFuturesMode, str], dict[str, Any]] = {}
        self._quote_timestamps: dict[tuple[StockFuturesMode, str], float] = {}
        self._closed_snapshot_fetch_at: dict[tuple[StockFuturesMode, str], float] = {}
        self._errors: dict[tuple[StockFuturesMode, str], str] = {}

    def _new_api(self) -> Any:
        if self._api_factory is not None:
            return self._api_factory()
        simulation = os.getenv("SHIOAJI_SIMULATION", "false").lower() == "true"
        return sj.Shioaji(simulation=simulation)

    def _credentials(self) -> tuple[str, str]:
        return os.getenv("SHIOAJI_API_KEY", ""), os.getenv("SHIOAJI_SECRET_KEY", "")

    def _create_pool_connection(self, index: int) -> _PoolConnection:
        api_key, secret_key = self._credentials()
        if not api_key or not secret_key:
            raise RuntimeError("缺少 SHIOAJI_API_KEY 或 SHIOAJI_SECRET_KEY")
        api = self._new_api()
        # 共享池只承載 Stock Tick／FOP Quote，不需要委託成交回報。
        api.login(api_key=api_key, secret_key=secret_key, subscribe_trade=False)
        try:
            self._wait_for_contracts_ready(api)
        except Exception:
            try:
                api.logout()
            except Exception:
                pass
            raise
        pool = _PoolConnection(index=index, api=api)

        def callback(exchange: Any, quote: Any) -> None:
            try:
                self.on_quote(index, exchange, quote)
            except Exception as exc:
                logger.debug("[Stock Futures][pool=%s] Quote callback 失敗: %s", index, exc)

        setter = getattr(api, "set_on_quote_fop_v1_callback", None)
        if callable(setter):
            setter(callback)
        else:
            decorator_factory = getattr(api, "on_quote_fop_v1", None)
            if not callable(decorator_factory):
                raise AttributeError("Shioaji API 不支援 QuoteFOPv1 callback")
            decorator_factory()(callback)

        def stock_callback(exchange: Any, tick: Any) -> None:
            with self._lock:
                handler = self._stock_tick_handler
            if handler is None:
                return
            try:
                handler(exchange, tick)
            except Exception as exc:
                logger.debug("[Shared Quote][pool=%s] Stock tick callback 失敗: %s", index, exc)

        stock_setter = getattr(api, "set_on_tick_stk_v1_callback", None)
        if callable(stock_setter):
            stock_setter(stock_callback)
        else:
            stock_decorator_factory = getattr(api, "on_tick_stk_v1", None)
            if callable(stock_decorator_factory):
                stock_decorator_factory()(stock_callback)

        try:
            @api.quote.on_event
            def _event(resp_code: int, event_code: int, info: str, event: str):
                if event_code in (1, 2, 12):
                    pool.healthy = False
                    pool.error = f"event={event_code} resp={resp_code} {info} {event}"
                elif event_code in (0, 13, 16):
                    pool.healthy = True
                    if event_code in (0, 13):
                        pool.error = None
                    if event_code == 13:
                        threading.Thread(
                            target=self._resubscribe_pool,
                            args=(index,),
                            name=f"shared-quote-resubscribe-{index}",
                            daemon=True,
                        ).start()
        except Exception:
            pass

        logger.info("[Shared Quote] Shioaji pool #%s 登入成功", index)
        return pool

    def _wait_for_contracts_ready(self, api: Any) -> None:
        """login 後等待行情 Session 與商品檔就緒，避免立即訂閱的 NotReady。"""
        deadline = time.monotonic() + self._contract_ready_timeout
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                stock = _cached_stock_contract(api, "2330")
                if stock is None:
                    stock = api.contracts.get("2330")
                if stock is not None:
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(1.0)
        detail = f": {last_error}" if last_error is not None else ""
        raise RuntimeError(f"共享行情 Session／商品檔於期限內未就緒{detail}")

    def _ensure_pools(self) -> None:
        # 備援 Railway 專案不得偷偷建立共享行情登入；否則正式／備援各四條
        # 再加主連線，會超過同一 person_id 五條限制並讓 P2P Session 卡住。
        if _quote_deployment_role() != "primary":
            raise RuntimeError("備援服務不建立 Shioaji 股期連線，請使用正式行情服務")
        # FastAPI/Vercel 可能同時送入多批全市場訂閱；初始化必須單飛，否則
        # 多個執行緒會同時看到 current=0，建立出數條都叫 pool #0 的連線。
        with self._pool_init_lock:
            with self._lock:
                current = len(self._pools)
            for index in range(current, self._pool_size):
                try:
                    pool = self._create_pool_connection(index)
                except Exception as exc:
                    logger.warning("[Shared Quote] pool #%s 初始化失敗，稍後重試: %s", index, exc)
                    with self._lock:
                        has_usable_pool = bool(self._pools)
                    if not has_usable_pool:
                        raise
                    break
                with self._lock:
                    self._pools.append(pool)

    @staticmethod
    def _is_transient_session_error(value: Any) -> bool:
        message = str(value or "").upper().replace("_", "")
        return any(marker in message for marker in TRANSIENT_SESSION_MARKERS)

    def _rebuild_pools(self) -> None:
        """登出卡住的共享連線並在同一程序內重建，下一次請求會重新訂閱。"""
        with self._pool_init_lock:
            with self._lock:
                old_pools = list(self._pools)
                self._pools.clear()
                self._stock_assignments.clear()
                self._stock_contracts.clear()
                self._stock_errors.clear()
                self._assignments.clear()
                self._contracts.clear()
                self._targets.clear()
                self._last_resolved_at.clear()
                self._reverse_codes.clear()
                self._errors.clear()
            for pool in old_pools:
                try:
                    pool.api.logout()
                except Exception:
                    pass
            for index in range(self._pool_size):
                try:
                    fresh = self._create_pool_connection(index)
                except Exception as exc:
                    logger.warning("[Shared Quote] recovery pool #%s 初始化失敗: %s", index, exc)
                    break
                with self._lock:
                    self._pools.append(fresh)

    def _trigger_pool_recovery(self, quote_service: Any = None) -> None:
        now = time.monotonic()
        with self._lock:
            if self._pool_recovery_thread and self._pool_recovery_thread.is_alive():
                return
            if now - self._last_pool_recovery_at < self._pool_recovery_cooldown:
                return

            def recover() -> None:
                try:
                    time.sleep(self._pool_recovery_delay)
                    with self._lock:
                        recovered_while_waiting = self._last_subscription_success_at > now
                    if recovered_while_waiting:
                        logger.info("[Shared Quote] 訂閱已自行恢復，取消過期的 Session 重建排程")
                        return
                    recover_primary = getattr(quote_service, "recover_transient_p2p_session", None)
                    if callable(recover_primary):
                        logger.warning("[Shared Quote] 同步重建主 Shioaji P2P Session")
                        recover_primary("股期商品／Kbars P2P Session 未建立，執行自動重連")
                        # 主 QuoteService 重連會先等待 5 秒，再登入並恢復訂閱；讓
                        # c0 完成後才重建 c1～c4，避免五條連線同時搶登入名額。
                        time.sleep(15.0)
                    logger.warning("[Shared Quote] 偵測到大量暫時性 Session 失敗，重建共享連線")
                    self._rebuild_pools()
                finally:
                    with self._lock:
                        self._last_pool_recovery_at = time.monotonic()
                        self._pool_recovery_thread = None

            self._pool_recovery_thread = threading.Thread(
                target=recover,
                name="shared-quote-session-recovery",
                daemon=True,
            )
            self._pool_recovery_thread.start()

    @staticmethod
    def _pool_load(pool: _PoolConnection) -> int:
        return len(pool.subscriptions) + len(pool.stock_subscriptions)

    def shared_capacity(self) -> int:
        return self._pool_size * self._per_connection_cap

    def _resubscribe_pool(self, index: int) -> None:
        """SESSION_UP 後在同一條連線恢復現貨與股期訂閱，不額外 login。"""
        with self._lock:
            if index >= len(self._pools):
                return
            pool = self._pools[index]
            stock_contracts = [
                self._stock_contracts[code]
                for code, pool_index in self._stock_assignments.items()
                if pool_index == index and code in self._stock_contracts
            ]
            futures_contracts = [
                self._contracts[key]
                for key, pool_index in self._assignments.items()
                if pool_index == index and key in self._contracts
            ]
        for contract in stock_contracts:
            try:
                pool.api.subscribe(contract, quote_type=sj.QuoteType.Tick)
            except Exception as exc:
                logger.warning("[Shared Quote][pool=%s] 恢復現貨訂閱失敗: %s", index, exc)
        for contract in futures_contracts:
            try:
                pool.api.subscribe(contract, quote_type=sj.QuoteType.Quote)
            except Exception as exc:
                logger.warning("[Shared Quote][pool=%s] 恢復股期訂閱失敗: %s", index, exc)

    def _choose_pool(self, key: tuple[StockFuturesMode, str]) -> _PoolConnection:
        self._ensure_pools()
        with self._lock:
            assigned = self._assignments.get(key)
            if assigned is not None and assigned < len(self._pools):
                pool = self._pools[assigned]
                active = key in pool.subscriptions
                if pool.healthy and (active or self._pool_load(pool) < self._per_connection_cap):
                    return pool
            candidates = [
                p for p in self._pools
                if p.healthy and self._pool_load(p) < self._per_connection_cap
            ]
            if not candidates:
                raise RuntimeError("共享即時行情連線容量已滿")
            pool = min(candidates, key=self._pool_load)
            self._assignments[key] = pool.index
            return pool

    def _choose_stock_pool(self, code: str) -> _PoolConnection:
        self._ensure_pools()
        with self._lock:
            assigned = self._stock_assignments.get(code)
            if assigned is not None and assigned < len(self._pools):
                pool = self._pools[assigned]
                if pool.healthy and code in pool.stock_subscriptions:
                    return pool
            candidates = [
                p for p in self._pools
                if p.healthy and self._pool_load(p) < self._per_connection_cap
            ]
            if not candidates:
                raise RuntimeError("共享即時行情連線容量已滿")
            pool = min(candidates, key=self._pool_load)
            self._stock_assignments[code] = pool.index
            return pool

    @staticmethod
    def _resolve_stock_contract(api: Any, code: str) -> Any:
        contract = _cached_stock_contract(api, code)
        if contract is None:
            try:
                contract = api.contracts.get(code)
            except Exception:
                contract = None
        if contract is None:
            return None
        security_type = str(getattr(contract, "security_type", "")).upper()
        if security_type and "STK" not in security_type and "STOCK" not in security_type:
            return None
        return contract

    def ensure_stock_subscriptions(
        self,
        stock_codes: Iterable[str],
        tick_handler: Callable[[Any, Any], None],
    ) -> dict[str, Any]:
        """在 4 條共享連線上永久分配現貨 Tick；不使用 LRU 淘汰。"""
        codes = _normalize_codes(stock_codes)
        with self._lock:
            self._stock_tick_handler = tick_handler
        result: dict[str, Any] = {
            "requested": codes,
            "newly_subscribed": [],
            "already_subscribed": [],
            "failed": {},
            "assignments": {},
        }
        try:
            self._ensure_pools()
        except Exception as exc:
            result["failed"] = {code: str(exc) for code in codes}
            return result

        for code in codes:
            try:
                pool = self._choose_stock_pool(code)
                with self._lock:
                    active = code in pool.stock_subscriptions
                if active:
                    with self._lock:
                        pool.stock_subscriptions[code] = time.time()
                        pool.stock_subscriptions.move_to_end(code)
                    result["already_subscribed"].append(code)
                    result["assignments"][code] = pool.index
                    continue

                contract = self._resolve_stock_contract(pool.api, code)
                if contract is None:
                    raise ValueError(f"找不到股票合約：{code}")
                pool.api.subscribe(contract, quote_type=sj.QuoteType.Tick)
                with self._lock:
                    self._stock_contracts[code] = contract
                    self._stock_assignments[code] = pool.index
                    pool.stock_subscriptions[code] = time.time()
                    pool.stock_subscriptions.move_to_end(code)
                    self._stock_errors.pop(code, None)
                result["newly_subscribed"].append(code)
                result["assignments"][code] = pool.index
            except Exception as exc:
                with self._lock:
                    self._stock_errors[code] = str(exc)
                    self._stock_assignments.pop(code, None)
                result["failed"][code] = str(exc)

        transient_count = sum(
            1 for error in result["failed"].values()
            if self._is_transient_session_error(error)
        )
        if codes and transient_count >= max(1, len(codes) // 2):
            self._trigger_pool_recovery(quote_service)
        elif result["newly_subscribed"] or result["already_subscribed"]:
            with self._lock:
                self._last_subscription_success_at = time.monotonic()

        with self._lock:
            result["active_count"] = sum(len(p.stock_subscriptions) for p in self._pools)
            result["pool_counts"] = {
                str(p.index): len(p.stock_subscriptions) for p in self._pools
            }
            result["total_pool_counts"] = {
                str(p.index): self._pool_load(p) for p in self._pools
            }
        result["pool_size"] = self._pool_size
        result["per_connection_cap"] = self._per_connection_cap
        result["capacity"] = self.shared_capacity()
        return result

    def _register_mapping(self, pool_index: int, key: tuple[StockFuturesMode, str], contract: Any) -> None:
        for reverse_key, mapped in list(self._reverse_codes.items()):
            if mapped == key:
                self._reverse_codes.pop(reverse_key, None)
        for code in {_contract_code(contract), _target_code(contract)}:
            if code:
                self._reverse_codes[(pool_index, code)] = key

    def resolve_contract_context_by_code(self, futures_code: str) -> Optional[tuple[Any, str, Any]]:
        """取回已訂閱合約、實際月份代號，以及持有該合約的 Shioaji API。

        K 線歷史查詢必須使用與股票期貨訂閱相同、已完成 Session 初始化的
        API 連線；冷啟動時主現貨連線可能仍在 NotReady，不能拿它查股期。
        """
        requested = str(futures_code).strip().upper()
        if not requested:
            return None
        with self._lock:
            for key, contract in self._contracts.items():
                aliases = {
                    _contract_code(contract),
                    _target_code(contract),
                    str(self._targets.get(key, "") or "").strip().upper(),
                }
                if requested not in aliases:
                    continue
                canonical = str(self._targets.get(key, "") or _target_code(contract)).strip().upper()
                pool_index = self._assignments.get(key)
                if pool_index is None or pool_index >= len(self._pools):
                    continue
                return contract, canonical or requested, self._pools[pool_index].api
        return None

    def resolve_contract_by_code(self, futures_code: str) -> Optional[tuple[Any, str]]:
        """由畫面上的 R1 或實際交割月代號取回目前訂閱合約。

        回傳的 canonical code 固定使用 target_code，讓歷史 Kbars 與即時
        Quote callback 都落在同一個實際合約代號。
        """
        context = self.resolve_contract_context_by_code(futures_code)
        return (context[0], context[1]) if context is not None else None

    def history_api_candidates(self) -> list[Any]:
        """回傳已登入的共享 API，供歷史 Kbars 在 P2P Session 間備援。"""
        with self._lock:
            return [pool.api for pool in self._pools if pool.api is not None]

    def _unsubscribe_key(self, key: tuple[StockFuturesMode, str]) -> None:
        with self._lock:
            pool_index = self._assignments.get(key)
            pool = self._pools[pool_index] if pool_index is not None and pool_index < len(self._pools) else None
            contract = self._contracts.pop(key, None)
            self._targets.pop(key, None)
            self._last_resolved_at.pop(key, None)
            self._closed_snapshot_fetch_at.pop(key, None)
            if pool is not None:
                pool.subscriptions.pop(key, None)
            for reverse_key, mapped in list(self._reverse_codes.items()):
                if mapped == key:
                    self._reverse_codes.pop(reverse_key, None)
        if pool is not None and contract is not None:
            try:
                pool.api.subscribe
                pool.api.unsubscribe(contract, quote_type=sj.QuoteType.Quote)
            except Exception as exc:
                logger.debug("[Stock Futures] 取消訂閱 %s 失敗: %s", key, exc)

    def _subscribe_or_refresh(
        self,
        code: str,
        mode: StockFuturesMode,
        resolver_api: Any = None,
    ) -> tuple[str, Optional[dict[str, str]]]:
        key = (mode, code)
        pool = self._choose_pool(key)
        # 多連線登入時，輔助行情 Session 可先收到 Tick，但其 P2P 商品查詢
        # Session 仍是 NotReady。R1 合約統一由已就緒的主連線解析，再交給
        # 對應共享行情連線訂閱，避免冷啟動時所有股期都解析失敗。
        contract_api = resolver_api or pool.api
        now = time.time()
        with self._lock:
            active = key in pool.subscriptions
            old_target = self._targets.get(key, "")
            checked_at = self._last_resolved_at.get(key, 0.0)
        if active and now - checked_at < self._recheck_seconds:
            with self._lock:
                pool.subscriptions[key] = now
                pool.subscriptions.move_to_end(key)
            return "already", None

        try:
            fresh = resolve_front_month_contract(contract_api, code, mode)
        except Exception:
            # 主現貨連線冷啟動時 contracts_info 偶爾仍是 NotReady；共享行情
            # pool 在建立時已通過商品檔就緒檢查，直接用它重試即可。
            if contract_api is pool.api:
                raise
            fresh = resolve_front_month_contract(pool.api, code, mode)
            contract_api = pool.api
        fresh_target = _target_code(fresh)
        if active and old_target == fresh_target:
            with self._lock:
                self._contracts[key] = fresh
                self._targets[key] = fresh_target
                self._last_resolved_at[key] = now
                self._register_mapping(pool.index, key, fresh)
                pool.subscriptions[key] = now
                pool.subscriptions.move_to_end(key)
                self._errors.pop(key, None)
            return "already", None

        roll: Optional[dict[str, str]] = None
        if active:
            self._unsubscribe_key(key)
            with self._lock:
                self._assignments[key] = pool.index
            roll = {"underlying_code": code, "from": old_target, "to": fresh_target}

        if self._pool_load(pool) >= self._per_connection_cap:
            with self._lock:
                self._assignments.pop(key, None)
            pool = self._choose_pool(key)
            fresh = resolve_front_month_contract(contract_api, code, mode)
            fresh_target = _target_code(fresh)

        pool.api.subscribe(fresh, quote_type=sj.QuoteType.Quote)
        with self._lock:
            self._contracts[key] = fresh
            self._targets[key] = fresh_target
            self._last_resolved_at[key] = now
            self._assignments[key] = pool.index
            self._register_mapping(pool.index, key, fresh)
            pool.subscriptions[key] = now
            pool.subscriptions.move_to_end(key)
            self._errors.pop(key, None)
        logger.info("[Stock Futures] pool=%s %s %s: %s -> %s", pool.index, mode, code, _contract_code(fresh), fresh_target)
        return "new", roll

    def ensure_subscriptions(self, quote_service: Any, underlying_codes: Iterable[str], mode: StockFuturesMode) -> dict[str, Any]:
        codes = _normalize_codes(underlying_codes)
        result: dict[str, Any] = {
            "mode": mode,
            "requested": codes,
            "newly_subscribed": [],
            "already_subscribed": [],
            "rolled": [],
            "failed": {},
            "contract_policy": "R1-front-month-auto-roll",
        }
        if mode not in ("regular", "mini"):
            result["failed"] = {code: f"不支援模式：{mode}" for code in codes}
            return result
        # Railway 新容器會先開 HTTP、延遲登入主 Shioaji。股期請求若在這段
        # 窗口先建立 c1～c4，共享池會搶走第一個 P2P Session，導致主 c0
        # 永遠無法查 Kbars。正式環境固定等主線登入後才建立共享池。
        from quote_service import quote_deployment_role

        if quote_deployment_role() != "primary":
            result["failed"] = {code: "備援服務不建立 Shioaji 股期連線，請使用正式行情服務" for code in codes}
            return result
        quote_state = getattr(quote_service, "state", None) if quote_service is not None else None
        if quote_state is not None and not bool(getattr(quote_state, "logged_in", False)):
            result["failed"] = {code: "主行情連線登入中，請稍後重試" for code in codes}
            return result
        try:
            self._ensure_pools()
        except Exception as exc:
            result["failed"] = {code: str(exc) for code in codes}
            return result

        resolver_api = getattr(quote_service, "api", None)
        for code in codes:
            try:
                state, roll = self._subscribe_or_refresh(code, mode, resolver_api)
                if state == "new":
                    result["newly_subscribed"].append(code)
                else:
                    result["already_subscribed"].append(code)
                if roll:
                    result["rolled"].append(roll)
            except Exception as exc:
                with self._lock:
                    self._errors[(mode, code)] = str(exc)
                result["failed"][code] = str(exc)

        transient_count = sum(
            1 for error in result["failed"].values()
            if self._is_transient_session_error(error)
        )
        if codes and transient_count >= max(1, len(codes) // 2):
            self._trigger_pool_recovery(quote_service)
        elif result["newly_subscribed"] or result["already_subscribed"]:
            with self._lock:
                self._last_subscription_success_at = time.monotonic()

        with self._lock:
            result["active_count"] = sum(len(pool.subscriptions) for pool in self._pools)
            result["pool_counts"] = {str(pool.index): len(pool.subscriptions) for pool in self._pools}
            result["shared_pool_total_counts"] = {
                str(pool.index): self._pool_load(pool) for pool in self._pools
            }
        result["pool_size"] = self._pool_size
        result["per_connection_cap"] = self._per_connection_cap
        return result

    def _refresh_closed_snapshots(self, codes: list[str], mode: StockFuturesMode) -> None:
        """休市時以單次 Snapshot 補最近交易日 R1 收盤；盤中永遠不用此方法。"""
        if is_stock_futures_day_session():
            return
        now_ts = time.time()
        grouped: dict[int, list[tuple[tuple[StockFuturesMode, str], Any]]] = {}
        with self._lock:
            for code in codes:
                key = (mode, code)
                contract = self._contracts.get(key)
                pool_index = self._assignments.get(key)
                fetched_at = self._closed_snapshot_fetch_at.get(key, 0.0)
                quote = self._quotes.get(key)
                if contract is None or pool_index is None or pool_index >= len(self._pools):
                    continue
                if quote is not None and quote.get("data_source") == "shioaji_realtime_stock_futures":
                    # 已有上一交易日最後即時 Quote，直接保留，不額外消耗 Snapshot 流量。
                    continue
                if quote is not None and quote.get("data_source") == "shioaji_snapshot_stock_futures" and now_ts - fetched_at < self._closed_snapshot_recheck_seconds:
                    continue
                grouped.setdefault(pool_index, []).append((key, contract))

        for pool_index, entries in grouped.items():
            if not entries:
                continue
            with self._lock:
                pool = self._pools[pool_index] if pool_index < len(self._pools) else None
            if pool is None or not pool.healthy:
                continue
            contracts = [contract for _, contract in entries]
            try:
                snapshots = list(pool.api.snapshots(contracts) or [])
            except Exception as exc:
                logger.warning("[Stock Futures][pool=%s] 休市 Snapshot 失敗: %s", pool_index, exc)
                with self._lock:
                    for key, _ in entries:
                        self._errors[key] = f"休市 Snapshot 失敗: {exc}"
                continue

            by_code: dict[str, Any] = {}
            for snap in snapshots:
                snap_code = str(getattr(snap, "code", "") or "").strip().upper()
                if snap_code:
                    by_code[snap_code] = snap

            for index, (key, contract) in enumerate(entries):
                mode_value, underlying_code = key
                contract_code = _contract_code(contract)
                target_code = _target_code(contract)
                snapshot = by_code.get(contract_code) or by_code.get(target_code)
                if snapshot is None and index < len(snapshots):
                    snapshot = snapshots[index]
                if snapshot is None:
                    continue

                close = _safe_float(getattr(snapshot, "close", None))
                if close is None or close <= 0:
                    continue
                change_price = _safe_float(getattr(snapshot, "change_price", None))
                change_rate = _safe_float(getattr(snapshot, "change_rate", None))
                reference = close - change_price if change_price is not None else None
                market_dt = _snapshot_datetime(snapshot)
                received_at = datetime.now(TW_TZ).isoformat()
                payload = {
                    "underlying_code": underlying_code,
                    "mode": mode_value,
                    **_contract_public(contract),
                    "target_code": self._targets.get(key, target_code),
                    "callback_code": str(getattr(snapshot, "code", "") or target_code),
                    "pool_index": pool_index,
                    "exchange": str(getattr(getattr(snapshot, "exchange", None), "value", None) or getattr(snapshot, "exchange", "TAIFEX")),
                    "close": close,
                    "reference": reference,
                    "open": _safe_float(getattr(snapshot, "open", None)),
                    "high": _safe_float(getattr(snapshot, "high", None)),
                    "low": _safe_float(getattr(snapshot, "low", None)),
                    "avg_price": _safe_float(getattr(snapshot, "average_price", None)),
                    "price_chg": change_price,
                    "pct_chg": round(change_rate / 100.0, 8) if change_rate is not None else None,
                    "pct_chg_pct": change_rate,
                    "volume": _safe_int(getattr(snapshot, "volume", None)),
                    "total_volume": _safe_int(getattr(snapshot, "total_volume", None)),
                    "amount": _safe_float(getattr(snapshot, "amount", None)),
                    "total_amount": _safe_float(getattr(snapshot, "total_amount", None)),
                    "simtrade": False,
                    "quote_time": market_dt.isoformat(),
                    "received_at": received_at,
                    "data_source": "shioaji_snapshot_stock_futures",
                    "session": "08:45-13:45 Asia/Taipei",
                    "contract_policy": "R1-front-month-auto-roll",
                }
                with self._lock:
                    self._quotes[key] = payload
                    self._quote_timestamps[key] = now_ts
                    self._closed_snapshot_fetch_at[key] = now_ts
                    self._errors.pop(key, None)

    def on_quote(self, pool_index: int, exchange: Any, quote: Any) -> bool:
        callback_code = str(getattr(quote, "code", "") or "").strip().upper()
        with self._lock:
            key = self._reverse_codes.get((pool_index, callback_code))
        if key is None:
            return False
        quote_dt = _quote_datetime(getattr(quote, "datetime", None), quote)
        if not is_stock_futures_day_session(quote_dt):
            return False

        mode, underlying_code = key
        with self._lock:
            contract = self._contracts.get(key)
            subscribed_target = self._targets.get(key, "")
        if contract is None:
            return False

        close = _safe_float(getattr(quote, "close", None))
        price_chg = _safe_float(getattr(quote, "price_chg", None))
        raw_pct = _safe_float(getattr(quote, "pct_chg", None))
        reference = close - price_chg if close is not None and price_chg is not None else None
        exchange_value = getattr(exchange, "value", None) or str(exchange).split(".")[-1]
        now = datetime.now(TW_TZ)
        payload = {
            "underlying_code": underlying_code,
            "mode": mode,
            **_contract_public(contract),
            "target_code": subscribed_target or _target_code(contract),
            "callback_code": callback_code,
            "pool_index": pool_index,
            "exchange": str(exchange_value),
            "close": close,
            "reference": reference,
            "open": _safe_float(getattr(quote, "open", None)),
            "high": _safe_float(getattr(quote, "high", None)),
            "low": _safe_float(getattr(quote, "low", None)),
            "avg_price": _safe_float(getattr(quote, "avg_price", None)),
            "price_chg": price_chg,
            "pct_chg": round(raw_pct / 100.0, 8) if raw_pct is not None else None,
            "pct_chg_pct": raw_pct,
            "volume": _safe_int(getattr(quote, "volume", None)),
            "total_volume": _safe_int(getattr(quote, "total_volume", None)),
            "amount": _safe_float(getattr(quote, "amount", None)),
            "total_amount": _safe_float(getattr(quote, "total_amount", None)),
            "bid_side_total_vol": _safe_int(getattr(quote, "bid_side_total_vol", None)),
            "ask_side_total_vol": _safe_int(getattr(quote, "ask_side_total_vol", None)),
            "simtrade": bool(getattr(quote, "simtrade", False)),
            "quote_time": quote_dt.isoformat(),
            "received_at": now.isoformat(),
            "data_source": "shioaji_realtime_stock_futures",
            "session": "08:45-13:45 Asia/Taipei",
            "contract_policy": "R1-front-month-auto-roll",
        }
        with self._lock:
            self._quotes[key] = payload
            self._quote_timestamps[key] = time.time()
            self._closed_snapshot_fetch_at.pop(key, None)
            if pool_index < len(self._pools) and key in self._pools[pool_index].subscriptions:
                self._pools[pool_index].subscriptions[key] = time.time()
                self._pools[pool_index].subscriptions.move_to_end(key)
        if close is not None and close > 0 and not payload["simtrade"]:
            try:
                from market_data_hub import get_market_data_hub

                get_market_data_hub().on_futures_tick({
                    "code": callback_code or payload["target_code"] or payload["futures_code"],
                    "close": close,
                    "volume": payload["volume"],
                    "total_volume": payload["total_volume"],
                    "tick_time": quote_dt.isoformat(),
                    "received_at": now.isoformat(),
                    "data_source": "shioaji_realtime_stock_futures",
                })
            except Exception as exc:
                logger.debug("[Stock Futures] 即時 K 棒聚合失敗: %s", exc)
        return True

    def get_quotes(self, quote_service: Any, underlying_codes: Iterable[str], mode: StockFuturesMode, *, subscribe: bool = True) -> dict[str, Any]:
        codes = _normalize_codes(underlying_codes)
        subscription = self.ensure_subscriptions(quote_service, codes, mode) if subscribe else None
        session_open = is_stock_futures_day_session()
        if not session_open:
            self._refresh_closed_snapshots(codes, mode)

        data: dict[str, Any] = {}
        with self._lock:
            for code in codes:
                key = (mode, code)
                contract = self._contracts.get(key)
                target = self._targets.get(key, "")
                quote = self._quotes.get(key)
                ts = self._quote_timestamps.get(key)
                age = round(time.time() - ts, 1) if ts else None
                if quote is not None:
                    item = dict(quote)
                    is_snapshot = item.get("data_source") == "shioaji_snapshot_stock_futures"
                    item["quote_age_seconds"] = None if is_snapshot else age
                    item["quote_stale"] = False if is_snapshot and not session_open else (age is not None and age > 90)
                    data[code] = item
                else:
                    contract_data = _contract_public(contract) if contract is not None else {}
                    if target:
                        contract_data["target_code"] = target
                    data[code] = {
                        "underlying_code": code,
                        "mode": mode,
                        **contract_data,
                        "close": None,
                        "pct_chg": None,
                        "pct_chg_pct": None,
                        "quote_age_seconds": None,
                        "quote_stale": True,
                        "data_source": "shioaji_realtime_stock_futures" if session_open else "shioaji_snapshot_stock_futures",
                        "session": "08:45-13:45 Asia/Taipei",
                        "contract_policy": "R1-front-month-auto-roll",
                        "error": self._errors.get(key),
                    }
        return {
            "status": "ok",
            "mode": mode,
            "session": "08:45-13:45 Asia/Taipei",
            "session_clock_open": session_open,
            "contract_policy": "R1-front-month-auto-roll",
            "closed_market_source": "Shioaji Futures Snapshot" if not session_open else None,
            "count": len(codes),
            "data": data,
            "subscription": subscription,
        }

    def status(self, _quote_service: Any) -> dict[str, Any]:
        with self._lock:
            mappings = {}
            for key, contract in self._contracts.items():
                mode, underlying = key
                item = _contract_public(contract)
                if self._targets.get(key):
                    item["target_code"] = self._targets[key]
                item["pool_index"] = self._assignments.get(key)
                mappings[f"{mode}:{underlying}"] = item
            snapshot_count = sum(1 for quote in self._quotes.values() if quote.get("data_source") == "shioaji_snapshot_stock_futures")
            return {
                "enabled": bool(self._pools) and all(pool.healthy for pool in self._pools),
                "session": "08:45-13:45 Asia/Taipei",
                "session_clock_open": is_stock_futures_day_session(),
                "contract_policy": "R1-front-month-auto-roll",
                "pool_size": self._pool_size,
                "per_connection_cap": self._per_connection_cap,
                "pool_counts": {str(pool.index): len(pool.subscriptions) for pool in self._pools},
                "stock_pool_counts": {str(pool.index): len(pool.stock_subscriptions) for pool in self._pools},
                "total_pool_counts": {str(pool.index): self._pool_load(pool) for pool in self._pools},
                "pool_health": {str(pool.index): {"healthy": pool.healthy, "error": pool.error} for pool in self._pools},
                "active_subscription_count": sum(len(pool.subscriptions) for pool in self._pools),
                "active_stock_subscription_count": sum(len(pool.stock_subscriptions) for pool in self._pools),
                "cached_quote_count": len(self._quotes),
                "cached_closed_snapshot_count": snapshot_count,
                "mappings": mappings,
                "errors": {f"{mode}:{code}": msg for (mode, code), msg in self._errors.items()},
            }

    def shutdown(self) -> None:
        with self._lock:
            pools = list(self._pools)
            self._pools.clear()
            self._stock_assignments.clear()
            self._stock_contracts.clear()
        for pool in pools:
            try:
                pool.api.logout()
            except Exception:
                pass


_service: Optional[StockFuturesQuoteService] = None


def get_stock_futures_quote_service() -> StockFuturesQuoteService:
    global _service
    if _service is None:
        _service = StockFuturesQuoteService()
    return _service


def shutdown_stock_futures_quote_service() -> None:
    """Railway graceful shutdown 時明確登出全部共享行情連線。"""
    global _service
    service = _service
    _service = None
    if service is not None:
        service.shutdown()
