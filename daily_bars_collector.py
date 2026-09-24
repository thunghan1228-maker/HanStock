"""日K背景收集器：資料來源是官方 TWSE/TPEx 盤後資料（official_daily_bars.py），
跟 Shioaji 完全無關，不佔用即時報價訂閱額度。第一次啟動（bars_1d 是空的）會回補
約一年份；之後每次只補最近幾天，避免每輪都重新下載一整年。"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, timedelta

from daily_bars_store import daily_bars_storage_status, prune_old_daily_bars
from official_daily_bars import download_official_daily_bars

logger = logging.getLogger("hanstock.daily_bars_collector")
POLL_SECONDS = max(1800, int(os.getenv("HANSTOCK_DAILY_BARS_COLLECTOR_SECONDS", str(60 * 60))))
BACKFILL_DAYS = max(30, int(os.getenv("HANSTOCK_DAILY_BARS_BACKFILL_DAYS", "370")))
CATCHUP_DAYS = max(1, int(os.getenv("HANSTOCK_DAILY_BARS_CATCHUP_DAYS", "5")))
KEEP_DAYS = max(30, int(os.getenv("HANSTOCK_DAILY_BARS_KEEP_DAYS", "365")))
_started = False
_lock = threading.Lock()
_last_run: dict = {"running": False, "startedAt": None, "finishedAt": None, "result": None, "error": None}


def _needs_full_backfill(status: dict, *, today: date | None = None) -> bool:
    """barCount==0時當然要做全量回補；但就算barCount>0，只要最早的交易日
    比「全量回補天數」往前推算出來的門檻還新，就代表上次的全量回補中途被
    中斷過（例如Railway重新部署，背景執行緒直接被砍掉），資料只補了一部分
    就永遠卡住，之後每輪只做5天catchup，缺口永遠補不回來。official_daily_
    bars.py的_save_day是ON CONFLICT DO NOTHING、只補缺口，重跑全量範圍
    不會造成重複資料，成本可控，所以偵測到覆蓋範圍不夠就直接重跑全量。"""
    if status.get("barCount", 0) == 0:
        return True
    first_date_text = status.get("firstTradeDate")
    if not first_date_text:
        return True
    try:
        earliest = date.fromisoformat(str(first_date_text)[:10])
    except ValueError:
        return True
    # 留7天餘裕，避免剛好卡在門檻邊緣時被假日/停市影響誤判成沒補完。
    expected_earliest = (today or date.today()) - timedelta(days=BACKFILL_DAYS - 7)
    return earliest > expected_earliest


def collect_once() -> dict:
    needs_backfill = _needs_full_backfill(daily_bars_storage_status())
    days = BACKFILL_DAYS if needs_backfill else CATCHUP_DAYS
    result = download_official_daily_bars(days=days, run_triangle_scan=False)
    result["mode"] = "backfill" if needs_backfill else "catchup"
    result["pruned"] = prune_old_daily_bars(KEEP_DAYS)
    return result


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def collector_status() -> dict:
    """最近一輪收集的摘要（給 /api/hub/persistence/status 看上櫃備援有沒有補到）。"""
    result = _last_run.get("result") or {}
    failures = result.get("source_failures") or []
    return {
        "running": _last_run["running"], "startedAt": _last_run["startedAt"], "finishedAt": _last_run["finishedAt"],
        "error": _last_run["error"], "mode": result.get("mode"), "insertedBars": result.get("inserted_bars"),
        "sourceFailureCount": len(failures), "lastSourceFailures": failures[-3:],
        "yahooOtc": result.get("yahoo_otc"),
    }


def _loop() -> None:
    while True:
        _last_run.update({"running": True, "startedAt": _now_text(), "error": None})
        try:
            result = collect_once()
            _last_run["result"] = result
            logger.info("日K已更新: %s", result)
        except Exception as error:  # noqa: BLE001
            _last_run["error"] = f"{type(error).__name__}: {error}"[:300]
            logger.exception("日K背景收集失敗")
        finally:
            _last_run.update({"running": False, "finishedAt": _now_text()})
        time.sleep(POLL_SECONDS)


def start_daily_bars_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_DAILY_BARS_COLLECTOR_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            logger.info("日K背景收集器已停用")
            return False
        threading.Thread(target=_loop, name="hanstock-daily-bars-collector", daemon=True).start()
        _started = True
        logger.info("日K背景收集器已啟動，間隔=%ss，保留=%s個交易日", POLL_SECONDS, KEEP_DAYS)
        return True
