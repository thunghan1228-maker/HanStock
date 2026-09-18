"""HanStock 戰鬥版盤中 5 分鐘訊號背景收集器。

只從目前戰鬥版網站讀取固定分片的盤中訊號，寫入 Railway SQLite。
不再連線已停用的原始版網站。
"""

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
DEFAULT_BATTLE_SITE_URL = "https://hanstock-battle-minimal.thunghan8.chatgpt.site"
SITE_URL = os.getenv("HANSTOCK_BATTLE_SITE_URL", DEFAULT_BATTLE_SITE_URL).rstrip("/")
SHARD_COUNT = max(1, int(os.getenv("HANSTOCK_INTRADAY_SIGNAL_SHARD_COUNT", "18")))
POLL_SECONDS = max(
    15,
    int(os.getenv("HANSTOCK_INTRADAY_SIGNAL_COLLECTOR_SECONDS", "20")),
)

_started = False
_lock = threading.Lock()
_status_lock = threading.Lock()
_last_success_bucket: int | None = None
_status: dict[str, object] = {
    "started": False,
    "enabled": True,
    "sourceUrl": SITE_URL,
    "shardCount": SHARD_COUNT,
    "pollSeconds": POLL_SECONDS,
    "lastAttemptAt": None,
    "lastSuccessAt": None,
    "lastBucket": None,
    "successfulShards": 0,
    "received": 0,
    "inserted": 0,
    "lastError": None,
}


def _update_status(**values: object) -> None:
    with _status_lock:
        _status.update(values)


def collector_status() -> dict[str, object]:
    with _status_lock:
        return dict(_status)


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
            "Cache-Control": "no-cache",
            "User-Agent": "HanStock-Battle-IntradaySignalCollector/2.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except Exception as exc:  # noqa: BLE001
        logger.warning("戰鬥版盤中訊號分片抓取失敗 shard=%s: %s", shard, exc)
        return None


def collect_once() -> bool:
    global _last_success_bucket

    now = datetime.now(TAIPEI)
    if not _scan_window(now):
        return False

    # 給剛收完的 5 分 K 幾秒讓行情與前端計算完成。
    if now.minute % 5 == 0 and now.second < 8:
        return False

    bucket = _bucket(now)
    if _last_success_bucket == bucket:
        return False

    today = now.strftime("%Y-%m-%d")
    _update_status(
        lastAttemptAt=now.isoformat(timespec="seconds"),
        lastBucket=bucket,
        lastError=None,
    )

    total_received = 0
    total_inserted = 0
    successful_shards = 0

    for shard in range(SHARD_COUNT):
        payload = _fetch_shard(shard)
        if not isinstance(payload, dict):
            continue
        if payload.get("tradeDate") != today:
            logger.warning(
                "戰鬥版盤中訊號分片日期不符 shard=%s got=%s today=%s",
                shard,
                payload.get("tradeDate"),
                today,
            )
            continue

        try:
            payload_shard = int(payload.get("shard", -1))
            payload_shard_count = int(payload.get("shardCount", 0))
        except (TypeError, ValueError):
            logger.warning("戰鬥版盤中訊號分片識別格式錯誤 shard=%s", shard)
            continue

        if payload_shard != shard or payload_shard_count != SHARD_COUNT:
            logger.warning(
                "戰鬥版盤中訊號分片識別不符 expected=%s/%s got=%s/%s",
                shard,
                SHARD_COUNT,
                payload_shard,
                payload_shard_count,
            )
            continue

        signals = payload.get("signals") or []
        if not isinstance(signals, list):
            continue

        inserted = save_intraday_signals(
            [row for row in signals if isinstance(row, dict)]
        )
        total_received += len(signals)
        total_inserted += len(inserted)
        successful_shards += 1

    _update_status(
        successfulShards=successful_shards,
        received=total_received,
        inserted=total_inserted,
    )

    if successful_shards != SHARD_COUNT:
        error = (
            f"盤中訊號分片不完整 {successful_shards}/{SHARD_COUNT}"
            f"，received={total_received} inserted={total_inserted}"
        )
        _update_status(lastError=error)
        logger.warning("%s；稍後同一 5 分鐘 bucket 重試", error)
        return False

    _last_success_bucket = bucket
    _update_status(
        lastSuccessAt=now.isoformat(timespec="seconds"),
        lastError=None,
    )
    logger.info(
        "戰鬥版盤中訊號已永久保存: date=%s bucket=%s shards=%s received=%s inserted=%s",
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
        except Exception as exc:  # noqa: BLE001
            _update_status(lastError=str(exc))
            logger.exception("戰鬥版盤中訊號背景收集器例外")
        time.sleep(POLL_SECONDS)


def start_intraday_signal_collector() -> bool:
    global _started

    with _lock:
        if _started:
            return False

        disabled = (
            os.getenv("HANSTOCK_INTRADAY_SIGNAL_COLLECTOR_ENABLED", "true")
            .strip()
            .lower()
            in {"0", "false", "no", "off"}
        )
        if disabled:
            _update_status(enabled=False, started=False, lastError="disabled_by_env")
            logger.info("戰鬥版盤中訊號背景收集器已由環境變數停用")
            return False

        thread = threading.Thread(
            target=_loop,
            name="hanstock-battle-intraday-signal-collector",
            daemon=True,
        )
        thread.start()
        _started = True
        _update_status(enabled=True, started=True, lastError=None)
        logger.info(
            "戰鬥版盤中訊號背景收集器已啟動，來源=%s，分片=%s，間隔=%ss",
            SITE_URL,
            SHARD_COUNT,
            POLL_SECONDS,
        )
        return True
