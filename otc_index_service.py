"""Shioaji 1.7 櫃買指數行情服務。

職責：
1. 從 IND / OTC 合約動態辨識櫃買發行量加權指數（不硬編碼新版代碼）。
2. 先用 api.kbars() 補齊近幾個交易日正式 1 分 K（跨日，不是只有今天），
   再聚合 5 分 K，讓 MA20 等需要多根K棒的指標不用等到今天自己累積夠。
3. 訂閱 QuoteIdxV1，由 quote_service callback 將即時 Quote 傳入 OtcIndexHub。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

# 回補範圍：足夠涵蓋MA20所需的20根5分K，同時預留假日緩衝(一個長週末最多
# 3個日曆天沒有交易)。跟stock_history_service.py同樣手法(Shioaji歷史
# kbars()本來就支援跨日查詢，只是這裡舊code只查了trade_date~trade_date)。
BOOTSTRAP_CALENDAR_DAYS = 6
MIN_BARS_FOR_MA20 = 20
# 跨日rollover或kbars失敗後的自動重補間隔；kbars失敗最常見是當日歷史流量
# 額度被個股回補用完，隔幾分鐘再試一次成本很低。
BOOTSTRAP_RETRY_SECONDS = 120

import shioaji as sj

from otc_index import (
    OTC_INDEX_OFFICIAL_NAME,
    TW_TZ,
    aggregate_1m_to_5m,
    exchange_text,
    index_name_score,
    normalize_kbars_1m,
)
from history_sources import OTC_INDEX_CODE, fetch_finmind_minute_bars, fetch_yahoo_minute_bars
from otc_index_hub import get_otc_index_hub
from otc_index_store import load_index_bars_5m, save_index_bars_5m

logger = logging.getLogger("hanstock.otc_index_service")


def _short_error(exc: Exception) -> str:
    """備援失敗原因給小工具那一行字用：HTTP 錯誤只留狀態碼，別把整段回應 JSON 印到手機畫面上。"""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return f"HTTP {code}"
    return str(exc).splitlines()[0][:80] if str(exc) else type(exc).__name__


class OtcIndexService:
    def __init__(self) -> None:
        self.contract: Any = None
        self.contract_code: Optional[str] = None
        self.contract_name: Optional[str] = None
        self.last_error: Optional[str] = None
        self._bootstrap_lock = threading.Lock()
        self._last_bootstrap_attempt = float("-inf")
        self._bootstrap_thread: Optional[threading.Thread] = None

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
        """補齊近幾個交易日的 5 分 K：先問 Shioaji kbars，再跟本機已存的 5 分 K
        合併。kbars 失敗（例如當日歷史流量額度用完）時就靠本機資料撐住 MA20，
        而不是整天卡在「資料蒐集中」。"""
        hub = get_otc_index_hub()
        self._last_bootstrap_attempt = time.monotonic()
        now_dt = datetime.now(TW_TZ)
        trade_date = now_dt.strftime("%Y-%m-%d")
        start_date = (now_dt.date() - timedelta(days=BOOTSTRAP_CALENDAR_DAYS - 1)).isoformat()
        now_ms = int(now_dt.timestamp() * 1000)

        bars_1m: list[dict[str, Any]] = []
        kbars_5m: list[dict[str, Any]] = []
        kbars_error: Optional[str] = None
        try:
            kbars = api.kbars(contract=contract, start=start_date, end=trade_date)
            bars_1m = normalize_kbars_1m(kbars, trade_date=None, include_current=False, now_ms=now_ms)
            kbars_5m = aggregate_1m_to_5m(bars_1m, include_current=False, now_ms=now_ms)
            if not kbars_5m:
                kbars_error = "Shioaji kbars 回傳 0 根正式盤 K 棒（歷史流量額度用完或尚無資料）"
        except Exception as exc:
            kbars_error = f"櫃買指數歷史 Kbars 補齊失敗: {exc}"
        history_source = "shioaji"
        if kbars_error:
            logger.warning("[OTC Index] %s", kbars_error)
            # 永豐拿不到（額度用完最常見）就改向備援拿櫃買指數：Yahoo 1 分 K → Yahoo 5 分 K
            # （指數有時只給 5 分 K）→ FinMind 櫃買分 K；只有價量，MA20 夠用。每個來源失敗的
            # 原因都留在 error 裡，前端小工具跟健康檢查才看得出卡在哪。
            fallback_errors: list[str] = []
            fallbacks = (
                ("yahoo", lambda: fetch_yahoo_minute_bars(OTC_INDEX_CODE, start_date, trade_date, interval="1m")),
                ("yahoo5m", lambda: fetch_yahoo_minute_bars(OTC_INDEX_CODE, start_date, trade_date, interval="5m")),
                ("finmind", lambda: fetch_finmind_minute_bars(OTC_INDEX_CODE, start_date, trade_date, block_on_error=False)),
            )
            for source_name, runner in fallbacks:
                try:
                    fallback_1m = runner()
                except Exception as exc:  # noqa: BLE001
                    fallback_errors.append(f"{source_name}: {_short_error(exc)}")
                    continue
                current_minute_start = now_ms - (now_ms % 60_000)
                fallback_1m = [bar for bar in fallback_1m if int(bar["ts"]) < current_minute_start]
                fallback_5m = aggregate_1m_to_5m(fallback_1m, include_current=False, now_ms=now_ms) if fallback_1m else []
                if fallback_5m:
                    bars_1m, kbars_5m, history_source, kbars_error = fallback_1m, fallback_5m, source_name, None
                    break
                fallback_errors.append(f"{source_name}: 0 根")
            if kbars_error and fallback_errors:
                kbars_error = f"{kbars_error}；備援 " + "、".join(fallback_errors)
                logger.warning("[OTC Index] 備援也沒拿到: %s", "、".join(fallback_errors))

        stored_5m: list[dict[str, Any]] = []
        try:
            stored_5m = load_index_bars_5m(start_date, trade_date)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[OTC Index] 讀取本機 5 分 K 失敗: %s", exc)
        merged = {int(bar["ts"]): bar for bar in stored_5m}
        merged.update({int(bar["ts"]): bar for bar in kbars_5m})
        bars_5m = [merged[ts] for ts in sorted(merged)]
        if kbars_5m:
            try:
                save_index_bars_5m(kbars_5m)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[OTC Index] 寫入本機 5 分 K 失敗: %s", exc)

        ok = kbars_error is None or len(bars_5m) >= MIN_BARS_FOR_MA20
        hub.seed_today(bars_1m, bars_5m, trade_date, ok=ok, error=kbars_error)
        self.last_error = kbars_error
        logger.info(
            "[OTC Index] 歷史 5 分 K 補齊: %s=%d, 本機=%d, 合併=%d, range=%s~%s%s",
            history_source, len(kbars_5m), len(stored_5m), len(bars_5m), start_date, trade_date,
            "" if ok else "（未達 MA20 門檻，稍後自動重試）",
        )
        result: dict[str, Any] = {
            "ok": ok,
            "trade_date": trade_date,
            "bars_1m": len(bars_1m),
            "bars_5m": len(bars_5m),
            "stored_bars_5m": len(stored_5m),
            "source": history_source,
        }
        if kbars_error:
            result["error"] = kbars_error
        return result

    def ensure_bootstrapped(self, api: Any, *, run_in_background: bool = True) -> bool:
        """跨日 rollover 把歷史清掉、或上次 kbars 補齊失敗之後，自動再補一次；
        每 BOOTSTRAP_RETRY_SECONDS 最多一次。回傳這次有沒有真的觸發補齊。"""
        if api is None:
            return False
        if get_otc_index_hub().get_status().get("bootstrap_ok"):
            return False
        with self._bootstrap_lock:
            if time.monotonic() - self._last_bootstrap_attempt < BOOTSTRAP_RETRY_SECONDS:
                return False
            if self._bootstrap_thread is not None and self._bootstrap_thread.is_alive():
                return False
            self._last_bootstrap_attempt = time.monotonic()
            contract = self.contract
            if contract is None:
                # 連合約都沒解析成功（例如登入當下列合約失敗）：走完整的解析+補齊+訂閱。
                target = lambda: self.subscribe(api, bootstrap=True, force_resolve=True)  # noqa: E731
            else:
                target = lambda: self.bootstrap_today(api, contract)  # noqa: E731
            if not run_in_background:
                target()
                return True
            thread = threading.Thread(target=target, name="hanstock-otc-index-bootstrap", daemon=True)
            self._bootstrap_thread = thread
            thread.start()
        return True

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
