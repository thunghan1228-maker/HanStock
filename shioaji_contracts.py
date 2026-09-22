"""Shioaji 合約查找的共用小工具（不 import 專案其他模組，避免循環）。

Shioaji 1.7 的 ``api.contracts.get(code)`` 只回 ``BaseContract``：只有代號、交易所、類別，沒有
``day_trade``、``margin_trading_balance``、``short_selling_balance`` 這些欄位；登入後馬上就有、
成本趨近於零，訂閱行情、查 K 棒用它就夠。正式環境 2026-09-22 ``/api/hub/stock-flags`` 的
debug 顯示 2330 解析出來的就是 ``BaseContract``，所以融資／融券／可現股當沖整天都是「不知道」。

要欄位有兩條路，成本在這個 SDK 版本沒辦法離線驗證（要登入），所以只能在背景執行緒計時使用：
- ``api.contracts.info(base)``：個股資訊列（day_trade、margin_loan_ratio、short_margin_ratio、
  trading_suspended、short_selling_suspended、disposition_level、attention_flag…）
- ``api.Contracts.Stocks[code]``：完整 ``Stock`` 合約（day_trade、margin_trading_balance、
  short_selling_balance、update_date）
"""

from __future__ import annotations

from typing import Any

FULL_CONTRACT_FIELDS = ("day_trade", "margin_trading_balance", "short_selling_balance")


def is_full_contract(contract: Any) -> bool:
    """有 day_trade 等欄位的完整合約（Stock／Contract），不是只帶代號的 BaseContract。"""
    if contract is None:
        return False
    try:
        return all(hasattr(contract, name) for name in FULL_CONTRACT_FIELDS)
    except Exception:  # noqa: BLE001  # 編譯版容器的 __getattr__ 可能丟 KeyError 而不是 AttributeError
        return False


def _lookup(category: Any, code: str) -> Any:
    """ContractCategory 的查找：有 get 就用 get，沒有（編譯版的 __getattr__ 會說 has no group 'get'）
    就用 [code]；找不到回 None，其他錯誤往外丟給呼叫端記錄。"""
    getter = getattr(category, "get", None)
    if callable(getter):
        return getter(code)
    try:
        return category[code]
    except (KeyError, IndexError, TypeError):
        return None


def full_stock_contract(api: Any, code: str) -> Any:
    """從 Contracts.Stocks 拿完整的 Stock 合約；清單裡沒有回 None，容器本身出錯往外丟。"""
    if api is None:
        return None
    code = str(code or "").strip().upper()
    if not code:
        return None
    for container_name in ("Contracts", "contracts"):
        container = getattr(api, container_name, None)
        if container is None:
            continue
        for category_name in ("Stocks", "stocks"):
            category = getattr(container, category_name, None)
            if category is None:
                continue
            contract = _lookup(category, code)
            if is_full_contract(contract):
                return contract
    return None


def base_stock_contract(api: Any, code: str) -> Any:
    """``api.contracts.get(code)``：登入後馬上就有的 BaseContract（只有代號／交易所）。"""
    if api is None:
        return None
    contracts = getattr(api, "contracts", None)
    getter = getattr(contracts, "get", None)
    if not callable(getter):
        return None
    return getter(code)


def resolve_stock_contract(api: Any, code: str) -> Any:
    """訂閱／查 K 棒用：api.contracts.get 優先（跟舊版一樣、成本趨近零），拿不到才試
    Contracts.Stocks[code]；任何一邊出錯都當成沒有，不讓例外打斷呼叫端。"""
    try:
        contract = base_stock_contract(api, code)
    except Exception:  # noqa: BLE001
        contract = None
    if contract is None:
        try:
            contract = full_stock_contract(api, code)
        except Exception:  # noqa: BLE001
            contract = None
    return contract


def contracts_info(api: Any, contract: Any) -> Any:
    """``api.contracts.info(contract)`` 的個股資訊列；SDK 沒有這個方法回 None，查詢出錯往外丟。"""
    if api is None or contract is None:
        return None
    contracts = getattr(api, "contracts", None)
    getter = getattr(contracts, "info", None)
    if not callable(getter):
        return None
    return getter(contract)


def contracts_fetch_status(api: Any) -> Any:
    """合約清單下載狀態（Fetched／Fetching／Unfetch）；拿不到回 None。"""
    if api is None:
        return None
    for container_name in ("contracts", "Contracts"):
        container = getattr(api, container_name, None)
        if container is None:
            continue
        try:
            status = getattr(container, "status", None)
        except Exception:  # noqa: BLE001
            status = None
        if callable(status):
            try:
                status = status()
            except Exception:  # noqa: BLE001
                status = None
        if status is not None:
            return str(getattr(status, "value", None) or getattr(status, "name", None) or status)
    return None
