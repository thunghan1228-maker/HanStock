"""HanStock 單一股票期貨即時行情服務。

這個模組刻意和既有台指期 TickFOPv1 流程分開：
- 台指期仍使用 QuoteType.Tick + TickFOPv1。
- 股票期貨使用 QuoteType.Quote + QuoteFOPv1，避免大量股票期貨行情覆蓋既有台指期 latest tick。

合約政策：
- regular：一般股票期貨的 R1（近月連續月）
- mini：小型股票期貨的 R1（近月連續月）
- 不寫死交割月份；每次 ensure 都重新核對 R1 的 target_code，換月後自動切到新近月。

正式日盤時段：08:45～13:45（Asia/Taipei）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Iterable, Literal, Optional

import shioaji as sj

logger = logging.getLogger("hanstock.stock_futures")
TW_TZ = timezone(timedelta(hours=8))
StockFuturesMode = Literal["regular", "mini"]
DEFAULT_TOTAL_QUOTE_CAP = 190  # Shioaji 官方總上限 200，保留台指期/指數/維運餘量。


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
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
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        code = str(raw).strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


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

    # QuoteFOPv1 也提供 date/time 欄位；datetime 缺失時再組合。
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
    """台灣股票期貨日盤：週一～週五 08:45～13:45（含 13:45 收盤時點）。"""
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
    name = str(getattr(contract, "name", "") or "")
    return "小型" in name


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
    """以標的反查期貨，挑出一般/小型股票期貨的 R1 近月連續月。"""
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
        if str(getattr(contract, "underlying_code", "") or code).strip().upper() not in ("", code):
            continue
        candidates.append(contract)

    if not candidates:
        label = "小型股票期貨" if mode == "mini" else "股票期貨"
        raise ValueError(f"{code} 找不到{label} R1 近月合約")

    # 正常只有一個 R1；若資料源暫時重複，以交割月/代碼做穩定排序。
    candidates.sort(
        key=lambda c: (
            str(getattr(c, "delivery_month", "") or "999999"),
            _contract_code(c),
        )
    )
    return candidates[0]


class StockFuturesQuoteService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._callback_api_id: Optional[int] = None
        self._contracts: dict[tuple[StockFuturesMode, str], Any] = {}
        # 不能只從 contract 物件回讀舊 target_code：Shioaji 1.7 合約資訊可能原地更新。
        # 所以保存「實際訂閱當下」的 target_code，才能可靠偵測換月。
        self._targets: dict[tuple[StockFuturesMode, str], str] = {}
        self._reverse_codes: dict[str, tuple[StockFuturesMode, str]] = {}
        self._subscriptions: OrderedDict[tuple[StockFuturesMode, str], float] = OrderedDict()
        self._quotes: dict[tuple[StockFuturesMode, str], dict[str, Any]] = {}
        self._quote_timestamps: dict[tuple[StockFuturesMode, str], float] = {}
        self._errors: dict[tuple[StockFuturesMode, str], str] = {}
        self._total_quote_cap = _env_int(
            "SHIOAJI_TOTAL_QUOTE_CAP",
            DEFAULT_TOTAL_QUOTE_CAP,
            20,
            195,
        )

    def _reset_for_new_api(self, api: Any) -> None:
        api_id = id(api)
        with self._lock:
            if self._callback_api_id == api_id:
                return
            # Railway/Shioaji 重連後舊訂閱已不存在；保留 quotes 作畫面降級，但清空 active mapping。
            self._contracts.clear()
            self._targets.clear()
            self._reverse_codes.clear()
            self._subscriptions.clear()
            self._callback_api_id = None

    def _install_callback(self, api: Any) -> None:
        self._reset_for_new_api(api)
        api_id = id(api)
        with self._lock:
            if self._callback_api_id == api_id:
                return

        def callback(exchange: Any, quote: Any) -> None:
            try:
                self.on_quote(exchange, quote)
            except Exception as exc:
                logger.debug("[Stock Futures] QuoteFOPv1 callback 失敗: %s", exc)

        setter = getattr(api, "set_on_quote_fop_v1_callback", None)
        if callable(setter):
            setter(callback)
        else:
            decorator_factory = getattr(api, "on_quote_fop_v1", None)
            if not callable(decorator_factory):
                raise AttributeError("Shioaji API 不支援 QuoteFOPv1 callback")
            decorator_factory()(callback)

        with self._lock:
            self._callback_api_id = api_id
        logger.info("[Stock Futures] QuoteFOPv1 callback 已掛載 (api_id=%s)", api_id)

    def _register_mapping(self, key: tuple[StockFuturesMode, str], contract: Any) -> None:
        # 先清掉 key 的舊反向碼，再登記 R1 與目前 target_code；callback 可能回任一者。
        for code, mapped_key in list(self._reverse_codes.items()):
            if mapped_key == key:
                self._reverse_codes.pop(code, None)
        for code in {_contract_code(contract), _target_code(contract)}:
            if code:
                self._reverse_codes[code] = key

    def _unsubscribe_key(self, api: Any, key: tuple[StockFuturesMode, str]) -> None:
        with self._lock:
            contract = self._contracts.pop(key, None)
            self._targets.pop(key, None)
            self._subscriptions.pop(key, None)
            for code, mapped_key in list(self._reverse_codes.items()):
                if mapped_key == key:
                    self._reverse_codes.pop(code, None)
        if contract is not None:
            try:
                api.unsubscribe(contract, quote_type=sj.QuoteType.Quote)
            except Exception as exc:
                logger.debug("[Stock Futures] 取消訂閱 %s 失敗: %s", key, exc)

    def _active_total(self, quote_service: Any) -> int:
        try:
            spot_count = len(quote_service.get_active_stock_codes())
        except Exception:
            spot_count = 0
        with self._lock:
            futures_count = len(self._subscriptions)
        # +1 保留既有台指期 Tick 訂閱；OTC index/其他行情由 cap 預留空間吸收。
        return spot_count + futures_count + 1

    def _free_capacity(
        self,
        api: Any,
        quote_service: Any,
        protected: set[tuple[StockFuturesMode, str]],
    ) -> list[str]:
        evicted: list[str] = []
        while self._active_total(quote_service) >= self._total_quote_cap:
            victim: Optional[tuple[StockFuturesMode, str]] = None
            with self._lock:
                for key in self._subscriptions.keys():
                    if key not in protected:
                        victim = key
                        break
            if victim is not None:
                self._unsubscribe_key(api, victim)
                evicted.append(f"{victim[0]}:{victim[1]}")
                continue

            # 目前請求的股票期貨優先：必要時釋放最舊的現貨 Tick 訂閱。
            try:
                spot_codes = quote_service.get_active_stock_codes()
            except Exception:
                spot_codes = []
            if not spot_codes:
                break
            spot_victim = spot_codes[0]
            try:
                quote_service._unsubscribe_stock(spot_victim)  # QuoteService 既有 LRU 安全取消流程
                evicted.append(f"spot:{spot_victim}")
            except Exception:
                break
        return evicted

    def ensure_subscriptions(
        self,
        quote_service: Any,
        underlying_codes: Iterable[str],
        mode: StockFuturesMode,
    ) -> dict[str, Any]:
        codes = _normalize_codes(underlying_codes)
        result: dict[str, Any] = {
            "mode": mode,
            "requested": codes,
            "newly_subscribed": [],
            "already_subscribed": [],
            "rolled": [],
            "evicted": [],
            "failed": {},
            "contract_policy": "R1-front-month-auto-roll",
        }
        if mode not in ("regular", "mini"):
            result["failed"] = {code: f"不支援模式：{mode}" for code in codes}
            return result

        api = getattr(quote_service, "api", None)
        logged_in = bool(getattr(getattr(quote_service, "state", None), "logged_in", False))
        if api is None or not logged_in:
            result["failed"] = {code: "Shioaji 尚未登入" for code in codes}
            return result

        self._install_callback(api)
        protected = {(mode, code) for code in codes}

        for code in codes:
            key = (mode, code)
            try:
                fresh = resolve_front_month_contract(api, code, mode)
                fresh_target = _target_code(fresh)
                with self._lock:
                    old_target = self._targets.get(key, "")
                    active = key in self._subscriptions

                if active and old_target == fresh_target:
                    with self._lock:
                        self._contracts[key] = fresh
                        self._targets[key] = fresh_target
                        self._register_mapping(key, fresh)
                        self._subscriptions[key] = time.time()
                        self._subscriptions.move_to_end(key)
                        self._errors.pop(key, None)
                    result["already_subscribed"].append(code)
                    continue

                if active:
                    self._unsubscribe_key(api, key)
                    result["rolled"].append({
                        "underlying_code": code,
                        "from": old_target,
                        "to": fresh_target,
                    })

                result["evicted"].extend(self._free_capacity(api, quote_service, protected))
                if self._active_total(quote_service) >= self._total_quote_cap:
                    raise RuntimeError("Shioaji 行情訂閱總容量不足")

                api.subscribe(fresh, quote_type=sj.QuoteType.Quote)
                with self._lock:
                    self._contracts[key] = fresh
                    self._targets[key] = fresh_target
                    self._register_mapping(key, fresh)
                    self._subscriptions[key] = time.time()
                    self._subscriptions.move_to_end(key)
                    self._errors.pop(key, None)
                result["newly_subscribed"].append(code)
                logger.info(
                    "[Stock Futures] 訂閱 %s %s: %s -> %s",
                    mode,
                    code,
                    _contract_code(fresh),
                    fresh_target,
                )
            except Exception as exc:
                with self._lock:
                    self._errors[key] = str(exc)
                result["failed"][code] = str(exc)

        with self._lock:
            result["active_count"] = len(self._subscriptions)
        result["total_quote_cap"] = self._total_quote_cap
        return result

    def on_quote(self, exchange: Any, quote: Any) -> bool:
        callback_code = str(getattr(quote, "code", "") or "").strip().upper()
        with self._lock:
            key = self._reverse_codes.get(callback_code)
        if key is None:
            return False

        quote_dt = _quote_datetime(getattr(quote, "datetime", None), quote)
        # 股票期貨只使用日盤；夜盤或其他 FOP Quote 不寫入此族群。
        if not is_stock_futures_day_session(quote_dt):
            return False

        mode, underlying_code = key
        with self._lock:
            contract = self._contracts.get(key)
            subscribed_target = self._targets.get(key, "")
        if contract is None:
            return False

        now = datetime.now(TW_TZ)
        close = _safe_float(getattr(quote, "close", None))
        price_chg = _safe_float(getattr(quote, "price_chg", None))
        raw_pct = _safe_float(getattr(quote, "pct_chg", None))
        reference = close - price_chg if close is not None and price_chg is not None else None
        exchange_value = getattr(exchange, "value", None) or str(exchange).split(".")[-1]
        payload = {
            "underlying_code": underlying_code,
            "mode": mode,
            **_contract_public(contract),
            # contract 物件可能已被 Shioaji 更新；對外行情標示用實際訂閱當下 target。
            "target_code": subscribed_target or _target_code(contract),
            "callback_code": callback_code,
            "exchange": str(exchange_value),
            "close": close,
            "reference": reference,
            "open": _safe_float(getattr(quote, "open", None)),
            "high": _safe_float(getattr(quote, "high", None)),
            "low": _safe_float(getattr(quote, "low", None)),
            "avg_price": _safe_float(getattr(quote, "avg_price", None)),
            "price_chg": price_chg,
            # HanStock Hub 慣例 pct_chg 使用比例；另保留百分點欄位避免語意混淆。
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
            if key in self._subscriptions:
                self._subscriptions[key] = time.time()
                self._subscriptions.move_to_end(key)
        return True

    def get_quotes(
        self,
        quote_service: Any,
        underlying_codes: Iterable[str],
        mode: StockFuturesMode,
        *,
        subscribe: bool = True,
    ) -> dict[str, Any]:
        codes = _normalize_codes(underlying_codes)
        subscription = self.ensure_subscriptions(quote_service, codes, mode) if subscribe else None
        data: dict[str, Any] = {}
        with self._lock:
            for code in codes:
                key = (mode, code)
                contract = self._contracts.get(key)
                subscribed_target = self._targets.get(key, "")
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
                    if subscribed_target:
                        contract_data["target_code"] = subscribed_target
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

    def status(self, quote_service: Any) -> dict[str, Any]:
        with self._lock:
            mappings = {}
            for key, contract in self._contracts.items():
                mode, underlying = key
                item = _contract_public(contract)
                if self._targets.get(key):
                    item["target_code"] = self._targets[key]
                mappings[f"{mode}:{underlying}"] = item
            errors = {f"{mode}:{underlying}": message for (mode, underlying), message in self._errors.items()}
            active = len(self._subscriptions)
            cached = len(self._quotes)
        return {
            "enabled": bool(getattr(getattr(quote_service, "state", None), "logged_in", False)),
            "session": "08:45-13:45 Asia/Taipei",
            "session_clock_open": is_stock_futures_day_session(),
            "contract_policy": "R1-front-month-auto-roll",
            "active_subscription_count": active,
            "cached_quote_count": cached,
            "total_quote_cap": self._total_quote_cap,
            "mappings": mappings,
            "errors": errors,
        }


_service: Optional[StockFuturesQuoteService] = None


def get_stock_futures_quote_service() -> StockFuturesQuoteService:
    global _service
    if _service is None:
        _service = StockFuturesQuoteService()
    return _service
