"""Shioaji 1.7 櫃買指數行情服務。

職責：
1. 從 IND / OTC 合約動態辨識櫃買發行量加權指數（不硬編碼新版代碼）。
2. 先用 api.kbars() 補齊今日正式 1 分 K，再聚合 5 分 K。
3. 訂閱 QuoteIdxV1，由 quote_service callback 將即時 Quote 傳入 OtcIndexHub。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import shioaji as sj

from otc_index import (
    OTC_INDEX_OFFICIAL_NAME,
    TW_TZ,
    aggregate_1m_to_5m,
    exchange_text,
    index_name_score,
    normalize_kbars_1m,
)
from otc_index_hub import get_otc_index_hub

logger = logging.getLogger("hanstock.otc_index_service")


class OtcIndexService:
    def __init__(self) -> None:
        self.contract: Any = None
        self.contract_code: Optional[str] = None
        self.contract_name: Optional[str] = None
        self.last_error: Optional[str] = None

    def reset_contract(self) -> None:
        self.contract = None
        self.contract_code = None
        self.contract_name = None

    def resolve_contract(self, api: Any, *, force: bool = False) -> Any:
        if self.contract is not None and not force:
            return self.contract

        hub = get_otc_index_hub()
        override = str(__import__("os").getenv("SHIOAJI_OTC_INDEX_CODE", "")).strip().upper()
        if override:
            try:
                contract = api.contracts.get(override)
                if contract is None:
                    raise ValueError(f"找不到指定指數合約 {override}")
                if exchange_text(getattr(contract, "exchange", "")) != "OTC":
                    raise ValueError(f"{override} 不是 OTC 指數合約")
                info = api.contracts.info(contract)
                name = str(getattr(info, "name", "") or OTC_INDEX_OFFICIAL_NAME)
                self._accept(contract, name)
                hub.configure_contract(self.contract_code or override, self.contract_name or name)
                return contract
            except Exception as exc:
                logger.warning("[OTC Index] SHIOAJI_OTC_INDEX_CODE=%s 解析失敗: %s", override, exc)

        try:
            contracts = list(api.contracts.list(sj.SecurityType.Index))
        except Exception as exc:
            self.last_error = f"列出指數合約失敗: {exc}"
            hub.set_subscribed(False, self.last_error)
            raise

        best: tuple[int, Any, str] | None = None
        for contract in contracts:
            if exchange_text(getattr(contract, "exchange", "")) != "OTC":
                continue
            try:
                info = api.contracts.info(contract)
                name = str(getattr(info, "name", "") or "")
            except Exception as exc:
                logger.debug("[OTC Index] 讀取 %s info 失敗: %s", getattr(contract, "code", "?"), exc)
                continue
            score = index_name_score(name, getattr(contract, "exchange", "OTC"))
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, contract, name)

        if best is None:
            self.last_error = "找不到櫃買發行量加權指數合約"
            hub.set_subscribed(False, self.last_error)
            raise LookupError(self.last_error)

        _, contract, name = best
        self._accept(contract, name)
        hub.configure_contract(self.contract_code or "", self.contract_name or "")
        logger.info("[OTC Index] 合約辨識成功: %s %s", self.contract_code, self.contract_name)
        return contract

    def _accept(self, contract: Any, name: str) -> None:
        self.contract = contract
        self.contract_code = str(getattr(contract, "code", "") or "").strip().upper() or None
        self.contract_name = str(name or "").strip() or OTC_INDEX_OFFICIAL_NAME
        self.last_error = None

    def bootstrap_today(self, api: Any, contract: Any) -> dict[str, Any]:
        hub = get_otc_index_hub()
        trade_date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
        try:
            kbars = api.kbars(contract=contract, start=trade_date, end=trade_date)
            now_ms = int(datetime.now(TW_TZ).timestamp() * 1000)
            bars_1m = normalize_kbars_1m(
                kbars,
                trade_date=trade_date,
                include_current=False,
                now_ms=now_ms,
            )
            bars_5m = aggregate_1m_to_5m(
                bars_1m,
                include_current=False,
                now_ms=now_ms,
            )
            hub.seed_today(bars_1m, bars_5m, trade_date)
            logger.info(
                "[OTC Index] 歷史 Kbars 補齊完成: 1m=%d, 5m=%d, date=%s",
                len(bars_1m),
                len(bars_5m),
                trade_date,
            )
            return {
                "ok": bool(bars_5m),
                "trade_date": trade_date,
                "bars_1m": len(bars_1m),
                "bars_5m": len(bars_5m),
            }
        except Exception as exc:
            self.last_error = f"櫃買指數歷史 Kbars 補齊失敗: {exc}"
            hub.set_subscribed(False, self.last_error)
            logger.warning("[OTC Index] %s", self.last_error)
            return {
                "ok": False,
                "trade_date": trade_date,
                "bars_1m": 0,
                "bars_5m": 0,
                "error": str(exc),
            }

    def subscribe(self, api: Any, *, bootstrap: bool = True, force_resolve: bool = False) -> bool:
        hub = get_otc_index_hub()
        try:
            contract = self.resolve_contract(api, force=force_resolve)
            if bootstrap:
                self.bootstrap_today(api, contract)
            api.subscribe(contract, quote_type=sj.QuoteType.Quote)
            hub.set_subscribed(True)
            logger.info(
                "[OTC Index] 已請求訂閱 Quote: %s %s",
                self.contract_code,
                self.contract_name,
            )
            return True
        except Exception as exc:
            self.last_error = str(exc)
            hub.set_subscribed(False, self.last_error)
            logger.warning("[OTC Index] 訂閱失敗: %s", exc)
            return False

    def accepts_quote(self, quote: Any) -> bool:
        code = str(getattr(quote, "code", "") or "").strip().upper()
        if not code:
            return False
        return bool(self.contract_code and code == self.contract_code)


_service: Optional[OtcIndexService] = None


def get_otc_index_service() -> OtcIndexService:
    global _service
    if _service is None:
        _service = OtcIndexService()
    return _service
