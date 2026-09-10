"""隔日沖全市場推估已停用。

HanStock 不再背景執行全市場隔日沖歷史掃描，也不允許網站 API
因讀取排行而重新啟動昂貴的全市場歷史掃描。

主力副圖、1m/5m K 線與其他即時功能不受影響。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hanstock.daytrade_flow_collector")


def _disabled_full_market_scan(*_args, **_kwargs) -> bool:
    """相容保留 API 名稱，但永久阻止隔日沖全市場歷史掃描。"""
    logger.info("隔日沖全市場歷史掃描已永久停用；略過 scan request")
    return False


# daytrade_flow.py 的網站 endpoint 仍可能在請求排行時呼叫
# start_full_market_scan()。在 persistent_app 載入本 collector 時直接把
# 入口替換成 no-op，避免前端殘留請求重新啟動約 1900 檔的歷史掃描。
try:
    import daytrade_flow as _daytrade_flow

    _daytrade_flow.start_full_market_scan = _disabled_full_market_scan
except Exception:
    # 不阻斷 HanStock 主程式啟動；背景 collector 本身仍保持停用。
    logger.exception("無法套用隔日沖全市場掃描停用保護")


def collect_once(*_args, **_kwargs) -> bool:
    """保留相容介面，但不再執行昂貴的隔日沖全市場掃描。"""
    return False


def start_daytrade_flow_collector() -> bool:
    """隔日沖全市場背景收集器永久停用。"""
    logger.info("每日隔日沖全市場備份已停用（成本/效能最佳化）")
    return False
