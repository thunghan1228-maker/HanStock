"""個股可交易條件（融資／融券／可現股當沖／有股期）查詢。

融資/融券/可現股當沖三項直接讀 Shioaji Contract 物件本身的欄位（跟
daytrade_early_sell.py 解析 day_trade/short_selling_suspended 是同一個
Contract 來源），不是額外呼叫另一個 API；margin_trading_balance／
short_selling_balance 官方型別是 int，這裡採用 TWSE/TPEx 每日融資融券
適用狀態慣例（0=不適用、非0=適用）當旗標解讀，不是真的成交餘額數字。

「有股期」改成純靜態比對 stock_groups.py 裡的「股期標的」族群成員（
TWSE/TPEx 官方公告的股票期貨標的清單，這個專案已經維護在這個族群裡），
不用再呼叫 Shioaji 的 futures_by_underlying 查詢、也不用處理找不到標的
合約的例外，查詢成本趨近於零。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from shioaji_contracts import contracts_fetch_status, full_stock_contract, is_full_contract, upgrade_contract
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
_warmer_status: dict[str, Any] = {"lastRunAt": None, "resolved": 0, "unknown": 0, "rounds": 0, "credit": None}
WARM_INTERVAL_SECONDS = max(30, int(os.getenv("HANSTOCK_ELIGIBILITY_WARM_SECONDS", "60")))
# 融資／融券改以永豐「信用額度查詢」為準：credit_enquires 直接回每檔的融資成數（margin_loan_ratio）
# 與融券保證金成數（short_margin_ratio），成數大於 0 就是可融資／可融券，是券商本身的資料，
# 跟合約清單有沒有下載完、合約物件帶不帶餘額欄位都無關。每 30 分鐘查一輪、每次 50 檔。
CREDIT_REFRESH_SECONDS = max(300, int(os.getenv("HANSTOCK_CREDIT_ENQUIRE_SECONDS", "1800")))
CREDIT_BATCH_SIZE = 50
_credit_cache: dict[str, dict[str, Any]] = {}
_credit_status: dict[str, Any] = {"lastRunAt": None, "resolved": 0, "batches": 0, "missing": 0, "error": None}
# None = 還沒查過；不能用 0.0，time.monotonic() 在剛開機的容器裡可能小於 30 分鐘，第一輪會被冷卻時間擋掉。
_credit_last_run: Optional[float] = None
TW_TZ = timezone(timedelta(hours=8))
# 合約清單是登入後在背景下載的，還沒下載完時 Contract 物件拿得到但欄位全是空白（day_trade 是空字串、
# 餘額是 0）。正式環境 2026-09-22 就是這樣：啟動時先被查了一輪，全部被當成「不可融資／不可當沖」
# 永久快取，整天每一檔都顯示不可。day_trade 有真的值（Yes／OnlyBuy／No）才算查到。
_POPULATED_DAY_TRADE = {"yes", "onlybuy", "no"}


def _enum_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def has_stock_futures(code: str) -> bool:
    return str(code).strip().upper() in _FUTURES_UNDERLYING_CODES


def _today() -> str:
    return datetime.now(TW_TZ).strftime("%Y-%m-%d")


def contract_debug(code: str, *, service: Any = None) -> dict[str, Any]:
    """給 /api/hub/stock-flags 的診斷用：這檔合約原始欄位長什麼樣、合約清單下載狀態。"""
    info: dict[str, Any] = {"code": str(code).strip().upper(), "contract": None, "contractsStatus": None, "fullContract": None}
    try:
        resolved_service = service if service is not None else _default_service()
        api = getattr(resolved_service, "api", None)
        info["contractsStatus"] = contracts_fetch_status(api)
        contract = _stock_contract(resolved_service, info["code"])
        if contract is not None:
            info["contract"] = {
                "type": type(contract).__name__,
                "full": is_full_contract(contract),
                "day_trade": _enum_text(getattr(contract, "day_trade", None)),
                "margin_trading_balance": getattr(contract, "margin_trading_balance", None),
                "short_selling_balance": getattr(contract, "short_selling_balance", None),
                "update_date": getattr(contract, "update_date", None),
            }
        full = full_stock_contract(api, info["code"])
        info["fullContract"] = type(full).__name__ if full is not None else None
        with _cache_lock:
            info["credit"] = dict(_credit_cache.get(info["code"]) or {}) or None
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return info


def _default_service() -> Any:
    from quote_service import get_quote_service

    return get_quote_service()


def _stock_contract(service: Any, code: str) -> Any:
    """服務自己的解析器優先；拿到的只是 BaseContract（沒有 day_trade 等欄位）就再從
    Contracts.Stocks 換成完整合約，清單還沒下載完就維持原樣。"""
    contract = _resolve_stock_contract(service, code)
    return upgrade_contract(getattr(service, "api", None), code, contract)


def _credit_flags(row: Any) -> tuple[bool, bool]:
    def _int(name: str) -> int:
        try:
            return int(getattr(row, name, None) or (row.get(name) if hasattr(row, "get") else 0) or 0)
        except (TypeError, ValueError):
            return 0

    marginable = _int("margin_loan_ratio") > 0 or _int("margin_unit") > 0
    shortable = _int("short_margin_ratio") > 0 or _int("short_unit") > 0
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
                contract = _stock_contract(resolved_service, code)
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
                    code = str(getattr(row, "stock_id", None) or (row.get("stock_id") if hasattr(row, "get") else "") or "").strip().upper()
                    if not code:
                        continue
                    marginable, shortable = _credit_flags(row)
                    found[code] = {"marginable": marginable, "shortable": shortable, "updatedAt": status["lastRunAt"]}
            status["resolved"] = len(found)
            # 查成功但沒回這檔：可能是券商信用表沒有這檔（不可融資券），先維持「不知道」，把代號列出來對照。
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


def get_trading_eligibility(code: str, *, service: Any = None) -> dict[str, Any]:
    """回傳 {marginable, shortable, dayTradeEligible, hasStockFutures}。
    融資/融券/可現股當沖同一個交易日內不會變動，快取供高頻呼叫（例如
    每一筆大單訊號都要標註）重複使用，不用每次都重新解析合約。dayTrade
    Eligible對Yes(可雙向)跟OnlyBuy(限先買後賣)都視為可當沖，只是可操作
    方向不同；需要嚴格區分雙向可當沖的呼叫端(例如疑似隔日沖)應該直接讀
    day_trade本身，不要用這裡的布林值。"""
    global _cache_day
    code = str(code).strip().upper()
    today = _today()
    with _cache_lock:
        if _cache_day != today:  # 每個交易日重新查一次，融資券狀態每天都可能變
            _cache.clear()
            _cache_day = today
        cached = _cache.get(code)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "marginable": None,
        "shortable": None,
        "dayTradeEligible": None,
        "hasStockFutures": has_stock_futures(code),
    }
    populated = False
    try:
        resolved_service = service if service is not None else _default_service()
        contract = _stock_contract(resolved_service, code)
        if contract is not None:
            day_trade = _enum_text(getattr(contract, "day_trade", None))
            populated = day_trade in _POPULATED_DAY_TRADE
            if populated:
                result["marginable"] = bool(int(getattr(contract, "margin_trading_balance", 0) or 0))
                result["shortable"] = bool(int(getattr(contract, "short_selling_balance", 0) or 0))
                result["dayTradeEligible"] = day_trade in {"yes", "onlybuy"}
    except Exception:  # noqa: BLE001
        pass

    # 融資／融券以信用額度查詢為準（券商資料），合約餘額欄位只是備援。
    with _cache_lock:
        credit = _credit_cache.get(code)
    if credit:
        result["marginable"] = credit["marginable"]
        result["shortable"] = credit["shortable"]

    # 沒查到（未登入、合約清單還沒下載完、欄位空白）就不快取：回 None 代表「還不知道」，下次再查。
    if populated or credit:
        with _cache_lock:
            _cache[code] = result
    return result


def clear_trading_eligibility_cache() -> None:
    global _credit_last_run
    with _cache_lock:
        _cache.clear()
        _credit_cache.clear()
    _credit_last_run = None


def peek_trading_eligibility(code: str) -> Optional[dict[str, Any]]:
    """只讀快取、不查合約：給要一次回幾百檔的端點用，請求路徑不能卡在合約清單下載上。"""
    code = str(code).strip().upper()
    with _cache_lock:
        if _cache_day != _today():
            return None
        cached = _cache.get(code)
    return dict(cached) if cached is not None else None


def warm_trading_eligibility(codes: Iterable[str], *, service: Any = None) -> dict[str, Any]:
    """把一批代號逐一查過、填進快取；查不到（合約清單還沒下載完）的下一輪再試。"""
    codes = list(codes)
    credit_status = refresh_credit_flags(codes, service=service)
    with _cache_lock:
        _cache.clear()  # 信用額度剛更新過，讓每檔重新合併一次
    resolved = unknown = 0
    for code in codes:
        try:
            info = get_trading_eligibility(code, service=service)
        except Exception:  # noqa: BLE001
            info = {"marginable": None}
        if info.get("marginable") is None and info.get("dayTradeEligible") is None:
            unknown += 1
        else:
            resolved += 1
    status = {
        "lastRunAt": datetime.now(TW_TZ).isoformat(timespec="seconds"), "resolved": resolved, "unknown": unknown,
        "credit": credit_status,
    }
    with _cache_lock:
        _warmer_status.update(status)
        _warmer_status["rounds"] = int(_warmer_status.get("rounds") or 0) + 1
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
