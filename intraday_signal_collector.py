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
MAX_BACKOFF_SECONDS = max(
    POLL_SECONDS,
    min(300, int(os.getenv("HANSTOCK_INTRADAY_SIGNAL_MAX_BACKOFF_SECONDS", "120"))),
)
FETCH_TIMEOUT_SECONDS = max(
    5,
    min(45, int(os.getenv("HANSTOCK_INTRADAY_SIGNAL_FETCH_TIMEOUT_SECONDS", "15"))),
)
CIRCUIT_BREAKER_SHARD_FAILURES = max(
    2,
    min(10, int(os.getenv("HANSTOCK_INTRADAY_SIGNAL_BREAKER_FAILURES", "3"))),
)

_started = False
_lock = threading.Lock()
_status_lock = threading.Lock()
_last_success_bucket: int | None = None
_consecutive_failures = 0
_status: dict[str, object] = {
    "started": False,
    "enabled": True,
    "sourceUrl": SITE_URL,
    "shardCount": SHARD_COUNT,
    "pollSeconds": POLL_SECONDS,
    "maxBackoffSeconds": MAX_BACKOFF_SECONDS,
    "fetchTimeoutSeconds": FETCH_TIMEOUT_SECONDS,
    "circuitBreakerShardFailures": CIRCUIT_BREAKER_SHARD_FAILURES,
    "consecutiveFailures": 0,
    "backoffActive": False,
    "nextRetrySeconds": POLL_SECONDS,
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


def _retry_delay_seconds(failures: int) -> int:
    """20s → 40s → 80s → 120s（預設上限），成功後立即恢復正常輪詢。"""
    if failures <= 0:
        return POLL_SECONDS
    multiplier = 2 ** min(failures - 1, 8)
    return min(MAX_BACKOFF_SECONDS, POLL_SECONDS * multiplier)


def _record_failure(error: str) -> int:
    global _consecutive_failures
    _consecutive_failures += 1
    delay = _retry_delay_seconds(_consecutive_failures)
    _update_status(
        consecutiveFailures=_consecutive_failures,
        backoffActive=delay > POLL_SECONDS,
        nextRetrySeconds=delay,
        lastError=error,
    )
    return delay


def _record_success() -> None:
    global _consecutive_failures
    _consecutive_failures = 0
    _update_status(
        consecutiveFailures=0,
        backoffActive=False,
        nextRetrySeconds=POLL_SECONDS,
        lastError=None,
    )


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
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
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
    consecutive_shard_failures = 0

    for shard in range(SHARD_COUNT):
        payload = _fetch_shard(shard)
        if not isinstance(payload, dict):
            consecutive_shard_failures += 1
            if consecutive_shard_failures >= CIRCUIT_BREAKER_SHARD_FAILURES:
                logger.warning(
                    "盤中訊號來源連續 %s 個分片抓取失敗，啟動熔斷以避免無效請求",
                    consecutive_shard_failures,
                )
                break
            continue
        consecutive_shard_failures = 0
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
        delay = _record_failure(error)
        logger.warning(
            "%s；失敗退避 %ss 後重試（連續失敗=%s）",
            error,
            delay,
            _consecutive_failures,
        )
        return False

    _last_success_bucket = bucket
    _record_success()
    _update_status(
        lastSuccessAt=now.isoformat(timespec="seconds"),
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
            delay = _record_failure(str(exc))
            logger.exception(
                "戰鬥版盤中訊號背景收集器例外；退避 %ss 後重試",
                delay,
            )
        status = collector_status()
        delay = int(status.get("nextRetrySeconds") or POLL_SECONDS)
        time.sleep(max(POLL_SECONDS, min(MAX_BACKOFF_SECONDS, delay)))


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
