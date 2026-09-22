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
_warmer_status: dict[str, Any] = {"lastRunAt": None, "resolved": 0, "unknown": 0, "rounds": 0}
WARM_INTERVAL_SECONDS = max(30, int(os.getenv("HANSTOCK_ELIGIBILITY_WARM_SECONDS", "60")))
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
    info: dict[str, Any] = {"code": str(code).strip().upper(), "contract": None, "contractsStatus": None}
    try:
        resolved_service = service if service is not None else _default_service()
        api = getattr(resolved_service, "api", None)
        contracts = getattr(api, "contracts", None) if api is not None else None
        status = getattr(contracts, "status", None) if contracts is not None else None
        info["contractsStatus"] = str(getattr(status, "value", status)) if status is not None else None
        contract = _resolve_stock_contract(resolved_service, info["code"])
        if contract is not None:
            info["contract"] = {
                "type": type(contract).__name__,
                "day_trade": _enum_text(getattr(contract, "day_trade", None)),
                "margin_trading_balance": getattr(contract, "margin_trading_balance", None),
                "short_selling_balance": getattr(contract, "short_selling_balance", None),
                "update_date": getattr(contract, "update_date", None),
            }
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return info


def _default_service() -> Any:
    from quote_service import get_quote_service

    return get_quote_service()


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
        contract = _resolve_stock_contract(resolved_service, code)
        if contract is not None:
            day_trade = _enum_text(getattr(contract, "day_trade", None))
            populated = day_trade in _POPULATED_DAY_TRADE
            if populated:
                result["marginable"] = bool(int(getattr(contract, "margin_trading_balance", 0) or 0))
                result["shortable"] = bool(int(getattr(contract, "short_selling_balance", 0) or 0))
                result["dayTradeEligible"] = day_trade in {"yes", "onlybuy"}
    except Exception:  # noqa: BLE001
        pass

    # 沒查到（未登入、合約清單還沒下載完、欄位空白）就不快取：回 None 代表「還不知道」，下次再查。
    if populated:
        with _cache_lock:
            _cache[code] = result
    return result


def clear_trading_eligibility_cache() -> None:
    with _cache_lock:
        _cache.clear()


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
