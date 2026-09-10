"""隔日沖全市場推估已停用。

HanStock 不再背景執行全市場隔日沖歷史掃描，以降低 Railway CPU、記憶體與
網路流量。主力副圖、1m/5m K 線與其他即時功能不受影響。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hanstock.daytrade_flow_collector")


def collect_once(*_args, **_kwargs) -> bool:
    """保留相容介面，但不再執行昂貴的隔日沖全市場掃描。"""
    return False


def start_daytrade_flow_collector() -> bool:
    """隔日沖全市場背景收集器永久停用。"""
    logger.info("每日隔日沖全市場備份已停用（成本/效能最佳化）")
    return False
