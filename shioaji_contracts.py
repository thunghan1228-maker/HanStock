"""Shioaji 合約查找的共用小工具（不 import 專案其他模組，避免循環）。

Shioaji 1.7 的 ``api.contracts.get(code)`` 只回 ``BaseContract``：只有代號、交易所、類別，沒有
``day_trade``、``margin_trading_balance``、``short_selling_balance``、``update_date`` 這些欄位。
正式環境 2026-09-22 就是這樣：``/api/hub/stock-flags`` 的 debug 顯示 2330 解析出來的是
``BaseContract``，所以融資／融券／可現股當沖整天都是「不知道」。完整的 ``Stock`` 合約要從
``api.Contracts.Stocks[code]``（等同 ``api.contracts.Stocks``）拿；合約清單還在背景下載時拿不到，
才退回 ``BaseContract``，訂閱行情、查 K 棒兩種合約都能用。
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
    getter = getattr(category, "get", None)
    try:
        if callable(getter):
            return getter(code)
        return category[code]
    except Exception:  # noqa: BLE001
        return None


def full_stock_contract(api: Any, code: str) -> Any:
    """從 Contracts.Stocks 拿完整的 Stock 合約；清單還沒下載完或沒有這檔就回 None。"""
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
            try:
                category = getattr(container, category_name, None)
            except Exception:  # noqa: BLE001
                category = None
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
    try:
        return getter(code)
    except Exception:  # noqa: BLE001
        return None


def resolve_stock_contract(api: Any, code: str) -> Any:
    """完整合約優先，退回 BaseContract；兩邊都沒有回 None。"""
    contract = full_stock_contract(api, code)
    if contract is None:
        contract = base_stock_contract(api, code)
    return contract


def upgrade_contract(api: Any, code: str, contract: Any) -> Any:
    """已經有一個合約（可能是 BaseContract）時，能換成完整合約就換。"""
    if is_full_contract(contract):
        return contract
    full = full_stock_contract(api, code)
    return full if full is not None else contract


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
