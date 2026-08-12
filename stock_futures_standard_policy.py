"""股票期貨 R1 近月選擇政策。

Shioaji 的 futures_by_underlying 在公司行動後，可能同時回傳標準契約與調整型契約，
例如 QOFR1（標準）與 QO1R1（調整型）。兩者若是同一到期月份，應優先使用標準契約，
避免因字典序誤選成交極少的調整型契約；若調整型月份更近，仍以最近月份為優先。
"""

from __future__ import annotations

from typing import Any

import stock_futures_service as service


def _is_standard_r1(contract: Any) -> bool:
    """台灣股票期貨標準連續月商品代碼以 F + R1 結尾；調整型常見為 1R1/2R1。"""
    return service._contract_code(contract).endswith("FR1")


def resolve_front_month_contract(api: Any, underlying_code: str, mode: service.StockFuturesMode) -> Any:
    code = str(underlying_code).strip().upper()
    if mode not in ("regular", "mini"):
        raise ValueError(f"不支援股票期貨模式：{mode}")

    # Railway 冷啟動時，商品目錄通常已經載入，但 P2P contracts.get Session
    # 仍可能拋出 SessionNotEstablished。先使用本機商品目錄，真的找不到才
    # 呼叫遠端查詢，避免股期即時價正常、歷史 Kbars 卻整批變成空陣列。
    underlying = service._cached_stock_contract(api, code)
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

    candidates: list[Any] = []
    for contract in list(finder(underlying) or []):
        ccode = service._contract_code(contract)
        if not ccode.endswith("R1"):
            continue
        is_mini = service._is_mini_contract(contract)
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

    # 1) 最近交割月份；2) 同月份優先標準契約；3) 代碼穩定排序。
    candidates.sort(
        key=lambda contract: (
            str(getattr(contract, "delivery_month", "") or "999999"),
            0 if _is_standard_r1(contract) else 1,
            service._contract_code(contract),
        )
    )
    return candidates[0]


def install() -> None:
    if getattr(service, "_hanstock_standard_stock_futures_policy_v1", False):
        return
    service.resolve_front_month_contract = resolve_front_month_contract
    service._hanstock_standard_stock_futures_policy_v1 = True


install()
