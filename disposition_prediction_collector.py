"""收盤後背景收集器：依賴official_daily_bars.py先把今天的bars_1d寫好，收集FinMind
Phase 2(本益比/淨值比/融資融券)+Phase 3(當沖成交量/借券賣出成交量)資料、抓產業分類，
對43個官方族群股票跑disposition_prediction.py的14款判定，讓處置股預測結果每天自動
更新，不用手動觸發。一天只需要真正跑一次（收盤價當天不會再變），偵測到今天已經跑過
就跳過。"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from daily_bars_store import daily_bars_storage_status
from disposition_fundamentals_assembly import build_fundamentals_by_code
from disposition_phase3_assembly import build_phase3_by_code
from disposition_prediction import official_group_codes, run_universe_for_date
from finmind_broker_branch_collector import fetch_industry_by_code
from finmind_disposition_fundamentals_collector import collect_trade_date as collect_finmind_fundamentals
from finmind_disposition_phase3_collector import collect_trade_date as collect_finmind_phase3

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
    codes = official_group_codes()
    try:
        industry_by_code = fetch_industry_by_code()
    except Exception:  # noqa: BLE001
        logger.warning("抓產業分類失敗，這次先不套用同類股比較", exc_info=True)
        industry_by_code = {}
    fundamentals_result = collect_finmind_fundamentals(trade_date, list(codes))
    phase3_result = collect_finmind_phase3(trade_date, list(codes))
    fundamentals_by_code = build_fundamentals_by_code(trade_date, codes, industry_by_code=industry_by_code)
    phase3_by_code = build_phase3_by_code(trade_date, codes)
    for code, extra in phase3_by_code.items():
        fundamentals_by_code.setdefault(code, {}).update(extra)
    results = run_universe_for_date(
        trade_date, codes=codes, industry_by_code=industry_by_code, fundamentals_by_code=fundamentals_by_code,
    )
    _last_run_date = trade_date
    fired_count = sum(1 for clauses in results.values() if any(r.fired for r in clauses))
    return {
        "status": "ok", "tradeDate": trade_date, "stockCount": len(results), "firedCount": fired_count,
        "fundamentals": fundamentals_result,
        "phase3": phase3_result,
    }


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
