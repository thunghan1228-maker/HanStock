"""HanStock Hub：每個已完成 5 分 K 從網站固定分片讀取訊號並永久寫入 /data SQLite。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from intraday_signal_store import save_intraday_signals

logger = logging.getLogger("hanstock.intraday_signal_collector")
TAIPEI = ZoneInfo("Asia/Taipei")
SITE_URL = os.getenv("HANSTOCK_SITE_URL", "https://www.hanstock.xyz").rstrip("/")
SHARD_COUNT = 18
POLL_SECONDS = max(15, int(os.getenv("HANSTOCK_INTRADAY_SIGNAL_COLLECTOR_SECONDS", "20")))

_started = False
_lock = threading.Lock()
_last_success_bucket: int | None = None


def _scan_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    # 第一根 09:00~09:05 收棒後開始；13:25~13:30 為最後一根。
    return 9 * 60 + 5 <= minutes <= 13 * 60 + 31


def _bucket(now: datetime) -> int:
    return int(now.timestamp() // 300)


def _fetch_shard(shard: int) -> dict | None:
    url = f"{SITE_URL}/api/intraday-compute?shard={shard}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "HanStock-Hub-IntradaySignalCollector/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except Exception as exc:  # noqa: BLE001
        logger.warning("盤中訊號分片抓取失敗 shard=%s: %s", shard, exc)
        return None


def collect_once() -> bool:
    global _last_success_bucket
    now = datetime.now(TAIPEI)
    if not _scan_window(now):
        return False
    # 給剛收完的 5 分 K 幾秒讓 Shioaji/Hub 完成聚合。
    if now.minute % 5 == 0 and now.second < 8:
        return False

    bucket = _bucket(now)
    if _last_success_bucket == bucket:
        return False

    today = now.strftime("%Y-%m-%d")
    total_received = 0
    total_inserted = 0
    successful_shards = 0

    for shard in range(SHARD_COUNT):
        payload = _fetch_shard(shard)
        if not isinstance(payload, dict):
            continue
        if payload.get("tradeDate") != today:
            logger.warning("盤中訊號分片日期不符 shard=%s got=%s today=%s", shard, payload.get("tradeDate"), today)
            continue
        if int(payload.get("shard") or -1) != shard or int(payload.get("shardCount") or 0) != SHARD_COUNT:
            logger.warning("盤中訊號分片識別不符 shard=%s", shard)
            continue
        signals = payload.get("signals") or []
        if not isinstance(signals, list):
            continue
        inserted = save_intraday_signals([row for row in signals if isinstance(row, dict)])
        total_received += len(signals)
        total_inserted += len(inserted)
        successful_shards += 1

    if successful_shards != SHARD_COUNT:
        logger.warning(
            "盤中訊號收集未完整，稍後同 bucket 重試: shards=%s/%s received=%s inserted=%s",
            successful_shards,
            SHARD_COUNT,
            total_received,
            total_inserted,
        )
        return False

    _last_success_bucket = bucket
    logger.info(
        "盤中訊號已永久保存: date=%s bucket=%s shards=%s received=%s inserted=%s",
        today,
        bucket,
        successful_shards,
        total_received,
        total_inserted,
    )
    return True


def _loop() -> None:
    while True:
        try:
            collect_once()
        except Exception:  # noqa: BLE001
            logger.exception("盤中訊號背景收集器例外")
        time.sleep(POLL_SECONDS)


def start_intraday_signal_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_INTRADAY_SIGNAL_COLLECTOR_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            logger.info("盤中訊號背景收集器已停用")
            return False
        thread = threading.Thread(target=_loop, name="hanstock-intraday-signal-collector", daemon=True)
        thread.start()
        _started = True
        logger.info(
            "盤中訊號背景收集器已啟動，來源=%s，分片=%s，間隔=%ss",
            SITE_URL,
            SHARD_COUNT,
            POLL_SECONDS,
        )
        return True
