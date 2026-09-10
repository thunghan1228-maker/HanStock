"""每分鐘把所有已訂閱股票的主力副圖 K 棒寫入 Railway Volume。"""

from __future__ import annotations

import logging
import os
import threading
import time

from main_force_store import save_main_force_batches

logger = logging.getLogger("hanstock.main_force_collector")
POLL_SECONDS = max(30, int(os.getenv("HANSTOCK_MAIN_FORCE_COLLECTOR_SECONDS", "60")))
LATEST_BARS_PER_CYCLE = max(1, int(os.getenv("HANSTOCK_MAIN_FORCE_LATEST_BARS", "2")))
_started = False
_lock = threading.Lock()


def collect_once(*, service=None, hub=None) -> dict[str, int]:
    if service is None:
        from quote_service import get_quote_service
        service = get_quote_service()
    if hub is None:
        from market_data_hub import get_market_data_hub
        hub = get_market_data_hub()
    codes = list(service.get_active_stock_codes() or [])
    rows_1m = []
    rows_5m = []
    for code in codes:
        # 每輪只更新目前棒與前一棒；較早的分鐘已永久保存，不需整天重寫。
        rows_1m.append((code, "1m", (hub.get_live_bars_1m(code) or [])[-LATEST_BARS_PER_CYCLE:]))
        rows_5m.append((code, "5m", (hub.get_live_bars(code) or [])[-LATEST_BARS_PER_CYCLE:]))
    saved_1m = save_main_force_batches(rows_1m)
    saved_5m = save_main_force_batches(rows_5m)
    return {"stockCount": len(codes), "saved1m": saved_1m, "saved5m": saved_5m}


def _loop() -> None:
    while True:
        try:
            result = collect_once()
            if result["saved1m"] or result["saved5m"]:
                logger.info("主力副圖已落盤: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("主力副圖背景保存失敗")
        time.sleep(POLL_SECONDS)


def start_main_force_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_MAIN_FORCE_COLLECTOR_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return False
        threading.Thread(target=_loop, name="hanstock-main-force-collector", daemon=True).start()
        _started = True
        return True
