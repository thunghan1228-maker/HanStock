"""收盤後背景收集器：依賴official_daily_bars.py先把今天的bars_1d寫好，對43個官方族群
股票跑disposition_prediction.py的14款判定，讓處置股預測結果每天自動更新，不用手動
觸發。一天只需要真正跑一次（收盤價當天不會再變），偵測到今天已經跑過就跳過。"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from daily_bars_store import daily_bars_storage_status
from disposition_prediction import official_group_codes, run_universe_for_date

logger = logging.getLogger("hanstock.disposition_prediction_collector")
TW_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = max(900, int(os.getenv("HANSTOCK_DISPOSITION_COLLECTOR_SECONDS", str(30 * 60))))
_started = False
_lock = threading.Lock()
_last_run_date: str | None = None


def _today_bars_ready(trade_date: str, status: dict | None = None) -> bool:
    status = status if status is not None else daily_bars_storage_status()
    return str(status.get("lastTradeDate") or "")[:10] == trade_date


def collect_once(*, now: datetime | None = None) -> dict:
    global _last_run_date
    trade_date = (now or datetime.now(TW_TZ)).strftime("%Y-%m-%d")
    if trade_date == _last_run_date:
        return {"status": "skipped", "reason": "今天已經跑過", "tradeDate": trade_date}
    if not _today_bars_ready(trade_date):
        return {"status": "waiting", "reason": "今天的bars_1d還沒寫好（等official_daily_bars.py先跑）", "tradeDate": trade_date}
    results = run_universe_for_date(trade_date, codes=official_group_codes())
    _last_run_date = trade_date
    fired_count = sum(1 for clauses in results.values() if any(r.fired for r in clauses))
    return {"status": "ok", "tradeDate": trade_date, "stockCount": len(results), "firedCount": fired_count}


def _loop() -> None:
    while True:
        try:
            result = collect_once()
            logger.info("處置股預測收集: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("處置股預測收集失敗")
        time.sleep(POLL_SECONDS)


def start_disposition_prediction_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_DISPOSITION_COLLECTOR_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            logger.info("處置股預測收集器已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-disposition-prediction-collector", daemon=True).start()
        _started = True
        logger.info("處置股預測收集器已啟動，間隔=%ss", POLL_SECONDS)
        return True
