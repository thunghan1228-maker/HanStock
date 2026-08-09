"""盤中每分鐘確認族群強弱，按 5 分鐘 bucket 永久寫入 Hub SQLite。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from group_strength_store import save_group_strength_snapshot

logger = logging.getLogger("hanstock.group_strength_collector")
TAIPEI = ZoneInfo("Asia/Taipei")
SITE_URL = os.getenv("HANSTOCK_SITE_URL", "https://www.hanstock.xyz").rstrip("/")
POLL_SECONDS = max(30, int(os.getenv("HANSTOCK_GROUP_STRENGTH_COLLECTOR_SECONDS", "60")))

_started = False
_lock = threading.Lock()


def _market_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 <= minutes <= 13 * 60 + 30


def _normalize_date(value: object) -> str | None:
    text = str(value or "").strip().replace("/", "-")
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def collect_once() -> bool:
    now = datetime.now(TAIPEI)
    if not _market_open(now):
        return False

    request = urllib.request.Request(
        f"{SITE_URL}/api/trpc/stocks.groupStrength",
        headers={"Accept": "application/json", "User-Agent": "HanStock-Hub-GroupStrengthCollector/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except Exception as exc:  # noqa: BLE001
        logger.warning("族群強弱抓取失敗: %s", exc)
        return False

    data = (((payload or {}).get("result") or {}).get("data") or {}).get("json") or {}
    if not data.get("marketDataAvailable"):
        return False

    trade_date = _normalize_date(data.get("sourceDate"))
    today = now.strftime("%Y-%m-%d")
    if trade_date != today:
        # 盤中若網站仍在回最近交易日收盤，不可誤存成今天的排名。
        return False

    try:
        bucket_ts = int(data.get("snapshotBucketTs") or 0)
    except (TypeError, ValueError):
        return False
    if bucket_ts <= 0:
        return False

    ranks: dict[str, int] = {}
    for row in data.get("rows") or []:
        if not isinstance(row, dict):
            continue
        group = str(row.get("group") or "").strip()
        rank = row.get("rank")
        if group and isinstance(rank, int) and rank > 0:
            ranks[group] = rank
    if not ranks:
        return False

    count = save_group_strength_snapshot(trade_date, bucket_ts, ranks)
    logger.info("族群強弱快照已保存: date=%s bucket=%s groups=%s snapshots=%s", trade_date, bucket_ts, len(ranks), count)
    return True


def _loop() -> None:
    # 每分鐘檢查一次；同一 5 分鐘 bucket 由 SQLite upsert 去重。
    while True:
        try:
            collect_once()
        except Exception:  # noqa: BLE001
            logger.exception("族群強弱背景收集器例外")
        time.sleep(POLL_SECONDS)


def start_group_strength_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_GROUP_STRENGTH_COLLECTOR_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            logger.info("族群強弱背景收集器已停用")
            return False
        thread = threading.Thread(target=_loop, name="hanstock-group-strength-collector", daemon=True)
        thread.start()
        _started = True
        logger.info("族群強弱背景收集器已啟動，來源=%s，間隔=%ss", SITE_URL, POLL_SECONDS)
        return True
