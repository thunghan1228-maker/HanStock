"""全市場族群強弱排行：用永豐 snapshots API 一次取得多檔股票報價。

刻意不用即時行情訂閱（quote_service.ensure_stock_subscriptions），
因為訂閱池只有 150 檔，全市場約 850 檔股票一次訂閱會把使用者正在看的
K 線圖／個股報價擠掉。snapshots 是一次性查詢，不佔用訂閱池。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from stock_groups import STOCK_GROUPS

logger = logging.getLogger("judystock.group_snapshot")

_cache: dict[str, Any] = {"result": None, "fetched_at": 0.0}
CACHE_SECONDS = 20
BATCH_SIZE = 400


def _all_codes() -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for stocks in STOCK_GROUPS.values():
        for code, _name in stocks:
            code = str(code).upper()
            if code not in seen:
                seen.add(code)
                codes.append(code)
    return codes


def _fetch_snapshots(api: Any, codes: list[str]) -> dict[str, Any]:
    contracts = []
    for code in codes:
        try:
            contract = api.Contracts.Stocks[code]
        except Exception:
            contract = None
        if contract is not None:
            contracts.append(contract)

    quotes: dict[str, Any] = {}
    for i in range(0, len(contracts), BATCH_SIZE):
        batch = contracts[i : i + BATCH_SIZE]
        try:
            snapshots = api.snapshots(batch)
        except Exception as exc:
            logger.warning("批次快照失敗（第 %s 批，%s 檔）：%s", i // BATCH_SIZE + 1, len(batch), exc)
            continue
        for snap in snapshots:
            code = str(getattr(snap, "code", "")).upper()
            if code:
                quotes[code] = snap
    return quotes


def compute_group_strength(quote_service: Any) -> dict[str, Any]:
    """回傳每個族群的平均漲跌幅排行；用快照 API，不佔用訂閱池。"""
    now = time.monotonic()
    cached = _cache["result"]
    if cached is not None and now - _cache["fetched_at"] < CACHE_SECONDS:
        return cached

    api = getattr(quote_service, "api", None)
    if api is None:
        raise RuntimeError("永豐尚未登入。")

    codes = _all_codes()
    quotes = _fetch_snapshots(api, codes)

    groups: list[dict[str, Any]] = []
    for group_name, stocks in STOCK_GROUPS.items():
        changes: list[float] = []
        for code, _name in stocks:
            snap = quotes.get(str(code).upper())
            rate = getattr(snap, "change_rate", None) if snap is not None else None
            if rate is None:
                continue
            try:
                changes.append(float(rate))
            except (TypeError, ValueError):
                continue
        if changes:
            groups.append(
                {
                    "group_name": group_name,
                    "avg_change_rate": round(sum(changes) / len(changes), 2),
                    "sample_count": len(changes),
                    "stock_count": len(stocks),
                }
            )

    groups.sort(key=lambda g: g["avg_change_rate"], reverse=True)
    result = {"quote_count": len(quotes), "requested_count": len(codes), "groups": groups}
    _cache["result"] = result
    _cache["fetched_at"] = now
    return result
