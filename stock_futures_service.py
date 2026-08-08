"""HanStock 股票期貨即時行情服務。

設計原則
- 股票期貨不使用現貨行情，也不使用 snapshots 輪詢冒充即時。
- 使用 Shioaji FOP Quote 訂閱；每一條連線都遠低於官方 200 訂閱上限。
- 預設建立 2 條「股票期貨專用」Shioaji 連線，與既有台指期/現貨主連線分離。
  247 檔一般股期 + 47 檔小型股期可平均分散到兩條專用連線。
- 每檔依股票標的反查 R1 近月連續月；不寫死交割月份。
- 定期重查 R1 target_code；換月時自動取消舊 target 並訂閱新 target。
- 日盤只接受 08:45～13:45（Asia/Taipei）的股票期貨 Quote。
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
DEFAULT_POOL_SIZE = 2
DEFAULT_PER_CONNECTION_CAP = 180
DEFAULT_FRONT_MONTH_RECHECK_SECONDS = 300.0


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


def resolve_front_month_contract(api: Any, underlying_code: str, mode: StockFuturesMode) -> Any:
    code = str(underlying_code).strip().upper()
    if mode not in ("regular", "mini"):
        raise ValueError(f"不支援股票期貨模式：{mode}")

    underlying = api.contracts.get(code)
    if underlying is None:
        try:
            underlying = api.Contracts.Stocks[code]
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


class StockFuturesQuoteService:
    def __init__(self, api_factory: Optional[Callable[[], Any]] = None) -> None:
        self._lock = threading.RLock()
        self._api_factory = api_factory
        self._pool_size = _env_int("SHIOAJI_STOCK_FUTURES_POOL_SIZE", DEFAULT_POOL_SIZE, 2, 4)
        self._per_connection_cap = _env_int(
            "SHIOAJI_STOCK_FUTURES_PER_CONNECTION_CAP",
            DEFAULT_PER_CONNECTION_CAP,
            100,
            190,
        )
        self._recheck_seconds = _env_float(
            "SHIOAJI_STOCK_FUTURES_R1_RECHECK_SECONDS",
            DEFAULT_FRONT_MONTH_RECHECK_SECONDS,
            30.0,
            3600.0,
        )
        self._pools: list[_PoolConnection] = []
        self._assignments: dict[tuple[StockFuturesMode, str], int] = {}
        self._contracts: dict[tuple[StockFuturesMode, str], Any] = {}
        self._targets: dict[tuple[StockFuturesMode, str], str] = {}
        self._last_resolved_at: dict[tuple[StockFuturesMode, str], float] = {}
        self._reverse_codes: dict[tuple[int, str], tuple[StockFuturesMode, str]] = {}
        self._quotes: dict[tuple[StockFuturesMode, str], dict[str, Any]] = {}
        self._quote_timestamps: dict[tuple[StockFuturesMode, str], float] = {}
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
        api.login(api_key=api_key, secret_key=secret_key)
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

        # 記錄連線事件；斷線後下一次 ensure 會重建該 pool。
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
        except Exception:
            pass

        logger.info("[Stock Futures] 專用 Shioaji pool #%s 登入成功", index)
        return pool

    def _ensure_pools(self) -> None:
        with self._lock:
            current = len(self._pools)
        for index in range(current, self._pool_size):
            pool = self._create_pool_connection(index)
            with self._lock:
                self._pools.append(pool)

    def _choose_pool(self, key: tuple[StockFuturesMode, str]) -> _PoolConnection:
        self._ensure_pools()
        with self._lock:
            assigned = self._assignments.get(key)
            if assigned is not None and assigned < len(self._pools):
                pool = self._pools[assigned]
                if pool.healthy:
                    return pool
            candidates = [p for p in self._pools if p.healthy and len(p.subscriptions) < self._per_connection_cap]
            if not candidates:
                raise RuntimeError("股票期貨專用即時行情連線容量已滿")
            pool = min(candidates, key=lambda p: len(p.subscriptions))
            self._assignments[key] = pool.index
            return pool

    def _register_mapping(self, pool_index: int, key: tuple[StockFuturesMode, str], contract: Any) -> None:
        for reverse_key, mapped in list(self._reverse_codes.items()):
            if mapped == key:
                self._reverse_codes.pop(reverse_key, None)
        for code in {_contract_code(contract), _target_code(contract)}:
            if code:
                self._reverse_codes[(pool_index, code)] = key

    def _unsubscribe_key(self, key: tuple[StockFuturesMode, str]) -> None:
        with self._lock:
            pool_index = self._assignments.get(key)
            pool = self._pools[pool_index] if pool_index is not None and pool_index < len(self._pools) else None
            contract = self._contracts.pop(key, None)
            self._targets.pop(key, None)
            self._last_resolved_at.pop(key, None)
            if pool is not None:
                pool.subscriptions.pop(key, None)
            for reverse_key, mapped in list(self._reverse_codes.items()):
                if mapped == key:
                    self._reverse_codes.pop(reverse_key, None)
        if pool is not None and contract is not None:
            try:
                pool.api.subscribe  # touch attribute before unsubscribe for clearer fake compatibility
                pool.api.unsubscribe(contract, quote_type=sj.QuoteType.Quote)
            except Exception as exc:
                logger.debug("[Stock Futures] 取消訂閱 %s 失敗: %s", key, exc)

    def _subscribe_or_refresh(self, code: str, mode: StockFuturesMode) -> tuple[str, Optional[dict[str, str]]]:
        key = (mode, code)
        pool = self._choose_pool(key)
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

        fresh = resolve_front_month_contract(pool.api, code, mode)
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
            # keep assignment on same healthy pool after unsubscribe
            with self._lock:
                self._assignments[key] = pool.index
            roll = {"underlying_code": code, "from": old_target, "to": fresh_target}

        if len(pool.subscriptions) >= self._per_connection_cap:
            # assignment may need to move if pool filled between resolve and subscribe
            with self._lock:
                self._assignments.pop(key, None)
            pool = self._choose_pool(key)
            fresh = resolve_front_month_contract(pool.api, code, mode)
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

    def ensure_subscriptions(self, _quote_service: Any, underlying_codes: Iterable[str], mode: StockFuturesMode) -> dict[str, Any]:
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
        try:
            self._ensure_pools()
        except Exception as exc:
            result["failed"] = {code: str(exc) for code in codes}
            return result

        for code in codes:
            try:
                state, roll = self._subscribe_or_refresh(code, mode)
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

        with self._lock:
            result["active_count"] = sum(len(pool.subscriptions) for pool in self._pools)
            result["pool_counts"] = {str(pool.index): len(pool.subscriptions) for pool in self._pools}
        result["pool_size"] = self._pool_size
        result["per_connection_cap"] = self._per_connection_cap
        return result

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
            if pool_index < len(self._pools) and key in self._pools[pool_index].subscriptions:
                self._pools[pool_index].subscriptions[key] = time.time()
                self._pools[pool_index].subscriptions.move_to_end(key)
        return True

    def get_quotes(self, quote_service: Any, underlying_codes: Iterable[str], mode: StockFuturesMode, *, subscribe: bool = True) -> dict[str, Any]:
        codes = _normalize_codes(underlying_codes)
        subscription = self.ensure_subscriptions(quote_service, codes, mode) if subscribe else None
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
                    item["quote_age_seconds"] = age
                    item["quote_stale"] = age is not None and age > 90
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
                        "data_source": "shioaji_realtime_stock_futures",
                        "session": "08:45-13:45 Asia/Taipei",
                        "contract_policy": "R1-front-month-auto-roll",
                        "error": self._errors.get(key),
                    }
        return {
            "status": "ok",
            "mode": mode,
            "session": "08:45-13:45 Asia/Taipei",
            "session_clock_open": is_stock_futures_day_session(),
            "contract_policy": "R1-front-month-auto-roll",
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
            return {
                "enabled": bool(self._pools) and all(pool.healthy for pool in self._pools),
                "session": "08:45-13:45 Asia/Taipei",
                "session_clock_open": is_stock_futures_day_session(),
                "contract_policy": "R1-front-month-auto-roll",
                "pool_size": self._pool_size,
                "per_connection_cap": self._per_connection_cap,
                "pool_counts": {str(pool.index): len(pool.subscriptions) for pool in self._pools},
                "pool_health": {str(pool.index): {"healthy": pool.healthy, "error": pool.error} for pool in self._pools},
                "active_subscription_count": sum(len(pool.subscriptions) for pool in self._pools),
                "cached_quote_count": len(self._quotes),
                "mappings": mappings,
                "errors": {f"{mode}:{code}": msg for (mode, code), msg in self._errors.items()},
            }

    def shutdown(self) -> None:
        with self._lock:
            pools = list(self._pools)
            self._pools.clear()
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
