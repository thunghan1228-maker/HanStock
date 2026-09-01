"""盤中每分鐘確認族群強弱，按 5 分鐘 bucket 永久寫入 Hub SQLite。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from group_strength_store import save_group_strength_snapshot

logger = logging.getLogger("hanstock.group_strength_collector")
TAIPEI = ZoneInfo("Asia/Taipei")
SITE_URL = os.getenv("HANSTOCK_SITE_URL", "https://www.hanstock.xyz").rstrip("/")
POLL_SECONDS = max(30, int(os.getenv("HANSTOCK_GROUP_STRENGTH_COLLECTOR_SECONDS", "60")))
MIN_RANKED_GROUPS = max(20, min(100, int(os.getenv("HANSTOCK_GROUP_STRENGTH_MIN_GROUPS", "40"))))
BUCKET_MS = 5 * 60 * 1000

_started = False
_lock = threading.Lock()


def _market_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 <= minutes <= 13 * 60 + 30


def _normalize_date(value: object) -> str | None:
    text = str(value or "").strip().replace("/", "-")
    if not text:
        return None
    for candidate, pattern in ((text[:10], "%Y-%m-%d"), (text[:8], "%Y%m%d")):
        try:
            return datetime.strptime(candidate, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _extract_ranks(data: dict[str, Any]) -> dict[str, int]:
    """兼容正式站有 rank 或只提供 avgChange 的族群資料格式。"""
    rows: list[tuple[str, int | None, float | None]] = []
    for row in data.get("rows") or []:
        if not isinstance(row, dict):
            continue
        group = str(row.get("group") or "").strip()
        if not group:
            continue
        raw_rank = row.get("rank")
        rank = int(raw_rank) if isinstance(raw_rank, (int, float)) and not isinstance(raw_rank, bool) and float(raw_rank).is_integer() and raw_rank > 0 else None
        try:
            avg_change = float(row.get("avgChange"))
        except (TypeError, ValueError):
            avg_change = None
        rows.append((group, rank, avg_change))

    explicit = {group: rank for group, rank, _ in rows if rank is not None}
    if len(explicit) >= MIN_RANKED_GROUPS:
        return explicit

    sortable = [(group, avg_change) for group, _, avg_change in rows if avg_change is not None]
    sortable.sort(key=lambda item: (-item[1], item[0]))
    return {group: index + 1 for index, (group, _) in enumerate(sortable)}


def _snapshot_identity(data: dict[str, Any], now: datetime) -> tuple[str, int] | None:
    """取得今日交易日與 5 分鐘 bucket，兼容新舊正式站欄位。"""
    today = now.strftime("%Y-%m-%d")
    source_value = data.get("sourceDate")
    trade_date = _normalize_date(source_value)
    if trade_date and trade_date != today:
        return None

    live_marker = data.get("liveData") if "liveData" in data else data.get("marketDataAvailable")
    if live_marker is False:
        return None
    if trade_date is None:
        # 來源未提供日期時，只有明確標示為即時資料才可落成今日快照。
        if live_marker is not True:
            return None
        trade_date = today

    raw_bucket = data.get("snapshotBucketTs") or data.get("snapshotTs")
    try:
        bucket_ts = int(raw_bucket or 0)
    except (TypeError, ValueError):
        bucket_ts = 0
    if 0 < bucket_ts < 1_000_000_000_000:
        bucket_ts *= 1000
    if bucket_ts <= 0:
        bucket_ts = int(now.timestamp() * 1000)
    bucket_ts = bucket_ts // BUCKET_MS * BUCKET_MS

    bucket_date = datetime.fromtimestamp(bucket_ts / 1000, TAIPEI).strftime("%Y-%m-%d")
    if bucket_date != trade_date:
        return None
    return trade_date, bucket_ts


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
    if not isinstance(data, dict):
        return False

    identity = _snapshot_identity(data, now)
    if identity is None:
        # 盤中若網站仍在回最近交易日收盤，不可誤存成今天的排名。
        return False
    trade_date, bucket_ts = identity

    ranks = _extract_ranks(data)
    if len(ranks) < MIN_RANKED_GROUPS:
        logger.warning("族群強弱資料不足，略過快照: groups=%s minimum=%s", len(ranks), MIN_RANKED_GROUPS)
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
