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

import threading
from typing import Any

from stock_bar_bootstrap import _resolve_stock_contract
from stock_groups import STOCK_GROUPS

_FUTURES_UNDERLYING_CODES = frozenset(
    str(code).strip().upper() for code, _name in STOCK_GROUPS.get("股期標的", [])
)

_cache_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


def _enum_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def has_stock_futures(code: str) -> bool:
    return str(code).strip().upper() in _FUTURES_UNDERLYING_CODES


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
    code = str(code).strip().upper()
    with _cache_lock:
        cached = _cache.get(code)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "marginable": None,
        "shortable": None,
        "dayTradeEligible": None,
        "hasStockFutures": has_stock_futures(code),
    }
    try:
        resolved_service = service if service is not None else _default_service()
        contract = _resolve_stock_contract(resolved_service, code)
        if contract is not None:
            result["marginable"] = bool(int(getattr(contract, "margin_trading_balance", 0) or 0))
            result["shortable"] = bool(int(getattr(contract, "short_selling_balance", 0) or 0))
            result["dayTradeEligible"] = _enum_text(getattr(contract, "day_trade", None)) in {"yes", "onlybuy"}
    except Exception:  # noqa: BLE001
        pass

    with _cache_lock:
        _cache[code] = result
    return result


def clear_trading_eligibility_cache() -> None:
    with _cache_lock:
        _cache.clear()
