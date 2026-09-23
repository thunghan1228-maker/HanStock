"""五分鐘K盤中訊號背景校正：收盤後(13:35+)用歷史kbars重播一次，修正
即時路徑受動態、有上限的股票訂閱時機影響、當天可能已經算錯或漏掉的
905/1+2多/創高黑龍等訊號。

跟stock_history_service.py判斷「今天」kbars是否已穩定的門檻(13:35)
保持一致：收盤前kbars對「今天」還常常沒到齊，回補只能拿到跟即時路徑
一樣不可靠的資料，等於白做；收盤後kbars才會是最終、不再變動的正式
資料，這時候回補才真的能修正掉當天用錯誤基準算出來的訊號。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from intraday_kline_signals import kline_signal_backfill_status, start_kline_signal_backfill_today

logger = logging.getLogger("hanstock.kline_signal_backfill_collector")
TW_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = max(60, int(os.getenv("HANSTOCK_KLINE_SIGNAL_BACKFILL_COLLECTOR_SECONDS", "600")))
SETTLE_HOUR, SETTLE_MINUTE = 13, 35
_started = False
_lock = threading.Lock()
_last_backfilled_date: str | None = None


def collect_once() -> dict:
    global _last_backfilled_date
    now = datetime.now(TW_TZ)
    trade_date_str = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5:
        return {"skipped": "weekend", "tradeDate": trade_date_str}
    if (now.hour, now.minute) < (SETTLE_HOUR, SETTLE_MINUTE):
        return {"skipped": "before_settlement", "tradeDate": trade_date_str}
    if _last_backfilled_date == trade_date_str:
        return {"skipped": "already_backfilled_today", "tradeDate": trade_date_str}

    started = start_kline_signal_backfill_today(trade_date=trade_date_str)
    if not started.get("started"):
        # 已經在跑了(可能是使用者自己打/api/hub/kline-signals/backfill-today
        # 手動觸發，或上一輪還沒結束)，這輪先不重複啟動，下一輪再檢查。
        return {"skipped": "backfill_already_running", "tradeDate": trade_date_str}

    # start_kline_signal_backfill_today是fire-and-forget，要等背景執行緒
    # 真的跑完才知道今天是否成功、能不能標記_last_backfilled_date；這條
    # collector本來就是自己專屬的執行緒在跑，這裡等待不會擋到其他collector
    # 或即時行情路徑。
    while kline_signal_backfill_status().get("running"):
        time.sleep(1.0)
    result = kline_signal_backfill_status().get("result") or {}
    if not result.get("error"):
        _last_backfilled_date = trade_date_str
    return {"tradeDate": trade_date_str, "result": result}


def _loop() -> None:
    while True:
        try:
            result = collect_once()
            logger.info("五分鐘K盤中訊號收盤後校正已檢查: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("五分鐘K盤中訊號收盤後校正失敗")
        time.sleep(POLL_SECONDS)


def start_kline_signal_backfill_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_KLINE_SIGNAL_BACKFILL_COLLECTOR_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            logger.info("五分鐘K盤中訊號收盤後校正已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-kline-signal-backfill-collector", daemon=True).start()
        _started = True
        logger.info("五分鐘K盤中訊號收盤後校正已啟動，間隔=%ss，門檻=%02d:%02d", POLL_SECONDS, SETTLE_HOUR, SETTLE_MINUTE)
        return True
