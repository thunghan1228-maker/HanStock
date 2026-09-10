"""櫃買指數服務（停用版）。

HanStock 已不再使用櫃買指數的即時行情、1m/5m K 棒與技術資料。
保留原有 class / singleton API，避免其他模組 import 時需要同步修改；
所有 Shioaji 合約解析、kbars 與 subscribe 都直接跳過，以降低連線與資料流量。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from otc_index_hub import get_otc_index_hub

logger = logging.getLogger("hanstock.otc_index_service")
_DISABLED_MESSAGE = "櫃買指數資料服務已停用"


class OtcIndexService:
    def __init__(self) -> None:
        self.contract: Any = None
        self.contract_code: Optional[str] = None
        self.contract_name: Optional[str] = None
        self.last_error: Optional[str] = _DISABLED_MESSAGE

    def reset_contract(self) -> None:
        self.contract = None
        self.contract_code = None
        self.contract_name = None
        self.last_error = _DISABLED_MESSAGE

    def resolve_contract(self, api: Any, *, force: bool = False) -> Any:
        self.last_error = _DISABLED_MESSAGE
        get_otc_index_hub().set_subscribed(False, _DISABLED_MESSAGE)
        return None

    def bootstrap_today(self, api: Any, contract: Any) -> dict[str, Any]:
        self.last_error = _DISABLED_MESSAGE
        get_otc_index_hub().set_subscribed(False, _DISABLED_MESSAGE)
        return {
            "ok": False,
            "trade_date": "",
            "bars_1m": 0,
            "bars_5m": 0,
            "error": _DISABLED_MESSAGE,
        }

    def subscribe(self, api: Any, *, bootstrap: bool = True, force_resolve: bool = False) -> bool:
        self.last_error = _DISABLED_MESSAGE
        get_otc_index_hub().set_subscribed(False, _DISABLED_MESSAGE)
        logger.info("[OTC Index] %s；略過合約解析、kbars 與 Shioaji 訂閱", _DISABLED_MESSAGE)
        return False

    def accepts_quote(self, quote: Any) -> bool:
        return False


_service: Optional[OtcIndexService] = None


def get_otc_index_service() -> OtcIndexService:
    global _service
    if _service is None:
        _service = OtcIndexService()
    return _service
