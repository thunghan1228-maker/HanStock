"""個股可交易條件（融資／融券／可現股當沖／有股期／處置等級）查詢。

資料來源（成本由低到高；2～4 只在背景暖機執行緒裡查，請求路徑只讀快取）：
1. 服務解析出來的合約物件本身：Shioaji 1.7 的 api.contracts.get 只回 BaseContract（沒有欄位），
   舊版 SDK 或測試替身給的是完整合約（有 day_trade／融資券餘額）就直接讀。
2. api.contracts.info(base)：合約清單下載完後的個股資訊列（day_trade、margin_loan_ratio、
   short_margin_ratio、trading_suspended、short_selling_suspended、disposition_level…）。
3. api.Contracts.Stocks[code]：完整 Stock 合約（day_trade、margin_trading_balance、short_selling_balance）。
4. api.credit_enquires：券商信用額度（融資成數／融券保證金成數），每 30 分鐘一輪、每批 50 檔，
   有查到就以它為準。

2 與 3 在這個 SDK 版本的成本沒辦法離線驗證（正式環境 2026-09-22 端點在請求路徑查合約就回 502），
所以每一輪都計時：單次超過 SLOW_CALL_SECONDS 或連續失敗 PATH_MAX_ERRORS 次就停用到下一輪，
狀態放在 warmer 的 paths 裡，正式環境看一眼就知道哪條路能走、哪條路慢。

「有股期」純靜態比對 stock_groups.py 的「股期標的」族群，查詢成本趨近於零。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from shioaji_contracts import contracts_fetch_status, contracts_info, full_stock_contract, is_full_contract
from stock_bar_bootstrap import _resolve_stock_contract
from stock_groups import STOCK_GROUPS

_FUTURES_UNDERLYING_CODES = frozenset(
    str(code).strip().upper() for code, _name in STOCK_GROUPS.get("股期標的", [])
)

logger = logging.getLogger("hanstock.trading_eligibility")
_cache_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}
_cache_day = ""
_warmer_started = False
_warmer_status: dict[str, Any] = {"lastRunAt": None, "resolved": 0, "unknown": 0, "rounds": 0, "credit": None, "paths": None}
_debug_cache: Optional[dict[str, Any]] = None
WARM_INTERVAL_SECONDS = max(30, int(os.getenv("HANSTOCK_ELIGIBILITY_WARM_SECONDS", "60")))
# 背景查詢的單次耗時上限：超過就停用那條路到下一輪，避免一條慢路把整輪暖機拖住。
SLOW_CALL_SECONDS = max(0.5, float(os.getenv("HANSTOCK_ELIGIBILITY_SLOW_SECONDS", "5")))
PATH_MAX_ERRORS = 3
PATH_NAMES = ("info", "stocks")
DEBUG_CODE = os.getenv("HANSTOCK_ELIGIBILITY_DEBUG_CODE", "2330").strip().upper()
# 融資／融券以永豐「信用額度查詢」為準：credit_enquires 直接回每檔的融資成數（margin_loan_ratio）
# 與融券保證金成數（short_margin_ratio），成數大於 0 就是可融資／可融券，是券商本身的資料，
# 跟合約清單有沒有下載完、合約物件帶不帶餘額欄位都無關。每 30 分鐘查一輪、每次 50 檔。
CREDIT_REFRESH_SECONDS = max(300, int(os.getenv("HANSTOCK_CREDIT_ENQUIRE_SECONDS", "1800")))
CREDIT_BATCH_SIZE = 50
_credit_cache: dict[str, dict[str, Any]] = {}
_credit_status: dict[str, Any] = {"lastRunAt": None, "resolved": 0, "batches": 0, "missing": 0, "error": None}
# None = 還沒查過；不能用 0.0，time.monotonic() 在剛開機的容器裡可能小於 30 分鐘，第一輪會被冷卻時間擋掉。
_credit_last_run: Optional[float] = None
_paths: dict[str, dict[str, Any]] = {}
TW_TZ = timezone(timedelta(hours=8))
# 合約清單是登入後在背景下載的，還沒下載完時 Contract 物件拿得到但欄位全是空白（day_trade 是空字串、
# 餘額是 0）。day_trade 有真的值（Yes／OnlyBuy／No）才算查到。
_POPULATED_DAY_TRADE = {"yes", "onlybuy", "no"}
_DAY_TRADE_ELIGIBLE = {"yes", "onlybuy"}
INFO_FIELDS = (
    "day_trade", "margin_loan_ratio", "short_margin_ratio", "margin_trading_balance", "short_selling_balance",
    "trading_suspended", "short_selling_suspended", "disposition_level", "attention_flag", "update_date",
)


def _enum_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    inner = getattr(value, "value", None)
    if inner is not None and inner is not value:
        return _jsonable(inner)
    return str(value)


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return int(value) if isinstance(value, bool) else None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return None
        return text in {"1", "true", "yes", "y", "t"}
    return bool(value)


def has_stock_futures(code: str) -> bool:
    return str(code).strip().upper() in _FUTURES_UNDERLYING_CODES


def _today() -> str:
    return datetime.now(TW_TZ).strftime("%Y-%m-%d")


def _default_service() -> Any:
    from quote_service import get_quote_service

    return get_quote_service()


# ---------------------------------------------------------------------------
# 背景查詢路徑的計時／停用
# ---------------------------------------------------------------------------
def _new_path() -> dict[str, Any]:
    return {"enabled": True, "calls": 0, "errors": 0, "maxMs": 0, "error": None, "disabledReason": None}


def _reset_paths() -> None:
    with _cache_lock:
        for name in PATH_NAMES:
            _paths[name] = _new_path()


def _path(name: str) -> dict[str, Any]:
    with _cache_lock:
        if name not in _paths:
            _paths[name] = _new_path()
        return _paths[name]


def _paths_snapshot() -> dict[str, Any]:
    with _cache_lock:
        return {name: dict(path) for name, path in _paths.items()}


def _timed(name: str, call: Callable[[], Any]) -> tuple[Any, bool]:
    """跑一次 call() 並記錄耗時／錯誤；太慢或連續失敗就停用這條路到下一輪。回 (結果, 是否成功)。"""
    path = _path(name)
    if not path["enabled"]:
        return None, False
    started = time.perf_counter()
    ok, result = True, None
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001
        ok = False
        path["error"] = f"{type(exc).__name__}: {exc}"[:160]
    elapsed = time.perf_counter() - started
    path["calls"] += 1
    path["maxMs"] = max(int(path["maxMs"]), int(round(elapsed * 1000)))
    if elapsed > SLOW_CALL_SECONDS:
        path["enabled"] = False
        path["disabledReason"] = f"單次 {elapsed:.1f} 秒超過 {SLOW_CALL_SECONDS:g} 秒上限，這一輪停用"
    elif not ok:
        path["errors"] += 1
        if path["errors"] >= PATH_MAX_ERRORS:
            path["enabled"] = False
            path["disabledReason"] = f"連續失敗 {PATH_MAX_ERRORS} 次（{path['error']}），這一輪停用"
    else:
        path["errors"] = 0
    return result, ok


# ---------------------------------------------------------------------------
# 各來源的欄位解讀
# ---------------------------------------------------------------------------
def _read(obj: Any, name: str, mapping: Optional[dict[str, Any]] = None) -> Any:
    try:
        value = getattr(obj, name, None)
    except Exception:  # noqa: BLE001
        value = None
    if value is None and mapping:
        value = mapping.get(name)
    return value


def _as_mapping(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    method = getattr(obj, "dict", None)
    if callable(method):
        try:
            data = method()
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            pass
    return {}


def _fields_from_contract(contract: Any) -> dict[str, Any]:
    """完整合約（舊版 Contract／Stock）：day_trade 有值才算查到，餘額欄位照 0=不適用、非 0=適用解讀。"""
    if not is_full_contract(contract):
        return {}
    day_trade = _enum_text(getattr(contract, "day_trade", None))
    if day_trade not in _POPULATED_DAY_TRADE:
        return {}
    return {
        "marginable": bool(_int_or_none(getattr(contract, "margin_trading_balance", 0)) or 0),
        "shortable": bool(_int_or_none(getattr(contract, "short_selling_balance", 0)) or 0),
        "dayTradeEligible": day_trade in _DAY_TRADE_ELIGIBLE,
    }


def _fields_from_info(info: Any) -> dict[str, Any]:
    """contracts.info 的個股資訊列：成數欄位優先（融資成數／融券保證金成數 > 0 就是可；正式環境
    2026-09-22 看到的是小數 0.6／0.9，不是 60／90，所以用浮點數比較），沒有成數才看餘額；
    另外帶出處置等級、注意股、暫停交易。"""
    if info is None:
        return {}
    mapping = _as_mapping(info)
    fields: dict[str, Any] = {}
    day_trade = _enum_text(_read(info, "day_trade", mapping))
    if day_trade in _POPULATED_DAY_TRADE:
        fields["dayTradeEligible"] = day_trade in _DAY_TRADE_ELIGIBLE
    loan_ratio = _float_or_none(_read(info, "margin_loan_ratio", mapping))
    short_ratio = _float_or_none(_read(info, "short_margin_ratio", mapping))
    if loan_ratio is not None:
        fields["marginable"] = loan_ratio > 0
    elif "dayTradeEligible" in fields:
        balance = _int_or_none(_read(info, "margin_trading_balance", mapping))
        if balance is not None:
            fields["marginable"] = balance != 0
    if short_ratio is not None:
        fields["shortable"] = short_ratio > 0 and not bool(_bool_or_none(_read(info, "short_selling_suspended", mapping)))
    elif "dayTradeEligible" in fields:
        balance = _int_or_none(_read(info, "short_selling_balance", mapping))
        if balance is not None:
            fields["shortable"] = balance != 0
    level = _int_or_none(_read(info, "disposition_level", mapping))
    if level is not None:
        fields["dispositionLevel"] = level
    attention = _bool_or_none(_read(info, "attention_flag", mapping))
    if attention is not None:
        fields["attention"] = attention
    suspended = _bool_or_none(_read(info, "trading_suspended", mapping))
    if suspended is not None:
        fields["tradingSuspended"] = suspended
    return fields


def _merge_missing(target: dict[str, Any], fields: dict[str, Any]) -> None:
    for key, value in fields.items():
        if target.get(key) is None:
            target[key] = value


# ---------------------------------------------------------------------------
# 信用額度查詢（融資券）
# ---------------------------------------------------------------------------
def _credit_flags(row: Any) -> tuple[bool, bool]:
    mapping = _as_mapping(row)

    def _num(name: str) -> float:
        return _float_or_none(_read(row, name, mapping)) or 0.0

    # 成數可能是 60／90 也可能是 0.6／0.9（個股資訊列就是小數），一律用浮點數比較；
    # 額度單位（margin_unit／short_unit）當天用完會是 0，所以成數與單位任一個 > 0 就算可。
    marginable = _num("margin_loan_ratio") > 0 or _num("margin_unit") > 0
    shortable = _num("short_margin_ratio") > 0 or _num("short_unit") > 0
    return marginable, shortable


def refresh_credit_flags(codes: Iterable[str], *, service: Any = None, force: bool = False) -> dict[str, Any]:
    """用 credit_enquires 批次查融資券可否，填進 _credit_cache；未登入或 API 沒這個方法就略過。"""
    global _credit_last_run
    now = time.monotonic()
    if not force and _credit_last_run is not None and now - _credit_last_run < CREDIT_REFRESH_SECONDS:
        with _cache_lock:
            return dict(_credit_status)
    _credit_last_run = now
    status: dict[str, Any] = {
        "lastRunAt": datetime.now(TW_TZ).isoformat(timespec="seconds"), "resolved": 0, "batches": 0, "missing": 0,
        "missingSample": [], "error": None,
    }
    try:
        resolved_service = service if service is not None else _default_service()
        api = getattr(resolved_service, "api", None)
        enquire = getattr(api, "credit_enquires", None)
        if api is None or not callable(enquire):
            status["error"] = "api 未登入或沒有 credit_enquires"
        else:
            contracts = []
            requested: list[str] = []
            for code in codes:
                code = str(code).strip().upper()
                contract = _resolve_stock_contract(resolved_service, code)
                if contract is not None:
                    contracts.append(contract)
                    requested.append(code)
            found: dict[str, dict[str, Any]] = {}
            for start in range(0, len(contracts), CREDIT_BATCH_SIZE):
                batch = contracts[start:start + CREDIT_BATCH_SIZE]
                try:
                    rows = enquire(batch, timeout=30000)
                except TypeError:
                    rows = enquire(batch)
                status["batches"] += 1
                for row in rows or []:
                    code = str(_read(row, "stock_id", _as_mapping(row)) or "").strip().upper()
                    if not code:
                        continue
                    marginable, shortable = _credit_flags(row)
                    found[code] = {"marginable": marginable, "shortable": shortable, "updatedAt": status["lastRunAt"]}
            status["resolved"] = len(found)
            # 查成功但沒回這檔：可能是券商信用表沒有這檔（不可融資券），先維持原判讀，把代號列出來對照。
            missing = [code for code in requested if code not in found] if found else []
            status["missing"] = len(missing)
            status["missingSample"] = missing[:10]
            if found:
                with _cache_lock:
                    _credit_cache.update(found)
    except Exception as exc:  # noqa: BLE001
        status["error"] = f"{type(exc).__name__}: {exc}"[:200]
        logger.warning("[Eligibility] 信用額度查詢失敗: %s", exc)
    with _cache_lock:
        _credit_status.update(status)
        return dict(_credit_status)


def _apply_credit_to_cache() -> None:
    """信用額度剛更新過：已快取的每檔直接覆蓋融資券旗標，不用重新查合約。"""
    with _cache_lock:
        for code, entry in _cache.items():
            credit = _credit_cache.get(code)
            if credit:
                entry["marginable"] = credit["marginable"]
                entry["shortable"] = credit["shortable"]


# ---------------------------------------------------------------------------
# 查詢
# ---------------------------------------------------------------------------
def _empty_result(code: str) -> dict[str, Any]:
    return {
        "marginable": None, "shortable": None, "dayTradeEligible": None, "hasStockFutures": has_stock_futures(code),
        "dispositionLevel": None, "source": None,
    }


def get_trading_eligibility(code: str, *, service: Any = None, deep: bool = False) -> dict[str, Any]:
    """回傳 {marginable, shortable, dayTradeEligible, hasStockFutures, dispositionLevel, source}。

    同一個交易日內不會變動，查到就快取供高頻呼叫重複使用。dayTradeEligible 對 Yes（可雙向）跟
    OnlyBuy（限先買後賣）都視為可當沖；需要嚴格區分的呼叫端請直接讀 day_trade。

    deep=False（請求路徑、訊號標註）只讀合約物件本身，成本趨近零；deep=True（背景暖機）才會走
    contracts.info 與 Contracts.Stocks 這兩條成本未知、有計時／停用機制的路。"""
    global _cache_day
    code = str(code).strip().upper()
    today = _today()
    with _cache_lock:
        if _cache_day != today:  # 每個交易日重新查一次，融資券狀態每天都可能變
            _cache.clear()
            _cache_day = today
        cached = _cache.get(code)
    if cached is not None:
        return dict(cached)

    result = _empty_result(code)
    sources: list[str] = []
    try:
        resolved_service = service if service is not None else _default_service()
        api = getattr(resolved_service, "api", None)
        contract = _resolve_stock_contract(resolved_service, code)
        fields = _fields_from_contract(contract)
        if fields:
            result.update(fields)
            sources.append("contract")
        if deep and contract is not None and api is not None and result["dayTradeEligible"] is None:
            info, ok = _timed("info", lambda: contracts_info(api, contract))
            fields = _fields_from_info(info) if ok else {}
            if fields:
                _merge_missing(result, fields)
                sources.append("info")
            if result["dayTradeEligible"] is None:
                full, ok = _timed("stocks", lambda: full_stock_contract(api, code))
                fields = _fields_from_contract(full) if ok else {}
                if fields:
                    _merge_missing(result, fields)
                    sources.append("stocks")
    except Exception:  # noqa: BLE001
        pass

    # 融資／融券以信用額度查詢為準（券商資料），合約欄位只是備援。
    with _cache_lock:
        credit = _credit_cache.get(code)
    if credit:
        result["marginable"] = credit["marginable"]
        result["shortable"] = credit["shortable"]
        sources.append("credit")
    result["source"] = "+".join(sources) or None

    # 沒查到（未登入、合約清單還沒下載完、欄位空白）就不快取：回 None 代表「還不知道」，下次再查。
    if result["dayTradeEligible"] is not None or result["marginable"] is not None:
        with _cache_lock:
            _cache[code] = dict(result)
    return result


def clear_trading_eligibility_cache() -> None:
    global _credit_last_run, _debug_cache
    with _cache_lock:
        _cache.clear()
        _credit_cache.clear()
        _credit_status.update({"lastRunAt": None, "resolved": 0, "batches": 0, "missing": 0, "error": None})
        _debug_cache = None
    _credit_last_run = None
    _reset_paths()


def peek_trading_eligibility(code: str) -> Optional[dict[str, Any]]:
    """只讀快取、不查合約：給要一次回幾百檔的端點用，請求路徑不能卡在合約清單下載上。"""
    code = str(code).strip().upper()
    with _cache_lock:
        if _cache_day != _today():
            return None
        cached = _cache.get(code)
    return dict(cached) if cached is not None else None


# ---------------------------------------------------------------------------
# 診斷
# ---------------------------------------------------------------------------
def inspect_contract(code: str, *, service: Any = None) -> dict[str, Any]:
    """真的去問 Shioaji：這檔合約原始欄位長什麼樣、contracts.info 回什麼、Contracts.Stocks 有沒有。
    只給背景暖機（結果放進 contract_debug 的快取）跟測試用，不要在請求路徑呼叫。"""
    code = str(code).strip().upper()
    info: dict[str, Any] = {
        "code": code, "contractsStatus": None, "contract": None, "info": None, "fullContract": None, "credit": None,
        "cached": None, "inspectedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"),
    }
    try:
        resolved_service = service if service is not None else _default_service()
        api = getattr(resolved_service, "api", None)
        info["contractsStatus"] = contracts_fetch_status(api)
        contract = _resolve_stock_contract(resolved_service, code)
        if contract is not None:
            info["contract"] = {
                "type": type(contract).__name__,
                "full": is_full_contract(contract),
                "day_trade": _enum_text(getattr(contract, "day_trade", None)),
                "margin_trading_balance": _jsonable(getattr(contract, "margin_trading_balance", None)),
                "short_selling_balance": _jsonable(getattr(contract, "short_selling_balance", None)),
                "update_date": _jsonable(getattr(contract, "update_date", None)),
            }
            if api is not None:
                row, ok = _timed("info", lambda: contracts_info(api, contract))
                if ok and row is not None:
                    mapping = _as_mapping(row)
                    info["info"] = {"type": type(row).__name__}
                    info["info"].update({name: _jsonable(_read(row, name, mapping)) for name in INFO_FIELDS})
                elif not ok:
                    info["info"] = {"error": _path("info").get("disabledReason") or _path("info").get("error")}
                full, ok = _timed("stocks", lambda: full_stock_contract(api, code))
                info["fullContract"] = type(full).__name__ if full is not None else None
                if not ok:
                    info["fullContractError"] = _path("stocks").get("disabledReason") or _path("stocks").get("error")
        with _cache_lock:
            info["credit"] = dict(_credit_cache.get(code) or {}) or None
            info["cached"] = dict(_cache.get(code) or {}) or None
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return info


def contract_debug(code: str = "") -> dict[str, Any]:
    """給 /api/hub/stock-flags 的診斷用：回背景暖機上一輪 inspect_contract 的結果，不碰 Shioaji。"""
    with _cache_lock:
        snapshot = dict(_debug_cache) if _debug_cache else None
    if snapshot:
        return snapshot
    return {"code": str(code or DEBUG_CODE).strip().upper(), "pending": True, "note": "背景暖機第一輪還沒跑完，稍後再看"}


# ---------------------------------------------------------------------------
# 背景暖機
# ---------------------------------------------------------------------------
def warm_trading_eligibility(codes: Iterable[str], *, service: Any = None) -> dict[str, Any]:
    """把一批代號查過、填進快取；查不到（合約清單還沒下載完）的下一輪再試。"""
    global _debug_cache
    codes = [str(code).strip().upper() for code in codes if str(code).strip()]
    _reset_paths()
    credit_status = refresh_credit_flags(codes, service=service)
    _apply_credit_to_cache()
    resolved = unknown = 0
    for code in codes:
        try:
            info = get_trading_eligibility(code, service=service, deep=True)
        except Exception:  # noqa: BLE001
            info = {"marginable": None, "dayTradeEligible": None}
        if info.get("marginable") is None and info.get("dayTradeEligible") is None:
            unknown += 1
        else:
            resolved += 1
    debug_code = DEBUG_CODE if DEBUG_CODE in codes else (codes[0] if codes else "")
    debug = inspect_contract(debug_code, service=service) if debug_code else None
    status = {
        "lastRunAt": datetime.now(TW_TZ).isoformat(timespec="seconds"), "resolved": resolved, "unknown": unknown,
        "credit": credit_status, "paths": _paths_snapshot(),
    }
    with _cache_lock:
        _warmer_status.update(status)
        _warmer_status["rounds"] = int(_warmer_status.get("rounds") or 0) + 1
        _debug_cache = debug
    return status


def trading_eligibility_warmer_status() -> dict[str, Any]:
    with _cache_lock:
        return dict(_warmer_status)


def start_trading_eligibility_warmer(codes_provider: Callable[[], Iterable[str]]) -> bool:
    """背景每分鐘把全部族群代號查一輪：登入後合約清單下載完成前查到的是空白（不快取），
    之後幾輪內就會填滿；端點只讀快取，不會因為合約清單還在下載而卡住回 502。"""
    global _warmer_started
    with _cache_lock:
        if _warmer_started:
            return False
        _warmer_started = True

    def _loop() -> None:
        while True:
            try:
                warm_trading_eligibility(list(codes_provider()))
            except Exception:  # noqa: BLE001
                logger.exception("[Eligibility] 背景更新失敗")
            time.sleep(WARM_INTERVAL_SECONDS)

    threading.Thread(target=_loop, name="hanstock-eligibility-warmer", daemon=True).start()
    return True
