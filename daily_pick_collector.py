"""Refresh the Battle daily picks without browser traffic; snapshots live in Sites D1."""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

TAIPEI = timezone(timedelta(hours=8))
SITE = "https://hanstock-battle-minimal.thunghan8.chatgpt.site"
logger = logging.getLogger("hanstock.daily_pick_collector")


def next_refresh_at(now: datetime, *, retry: bool = False) -> datetime:
    """Weekdays 14:30..23:50 Taipei. The destination validates exchange holidays."""
    local = now.astimezone(TAIPEI)
    start = local.replace(hour=14, minute=30, second=0, microsecond=0)
    if local.weekday() < 5 and local < start:
        return start
    if retry:
        candidate = local + timedelta(seconds=60)
    else:
        candidate = local.replace(second=0, microsecond=0) + timedelta(minutes=10 - local.minute % 10)
    if local.weekday() < 5 and candidate.date() == local.date():
        return candidate
    candidate = start + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


class DailyPickCollector:
    def __init__(self, *, site: str = SITE, clock=None, opener=None):
        self.url = site.rstrip("/") + "/api/daily-pick-list?scheduled=1"
        self.clock = clock or (lambda: datetime.now(TAIPEI))
        self.opener = opener or urlopen
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._status = {"state": "not_started", "nextAttemptAt": None,
                        "lastAttemptAt": None, "lastSuccessAt": None, "error": None}

    def status(self) -> dict:
        with self._lock:
            return {**self._status, "running": bool(self._thread and self._thread.is_alive()),
                    "endpoint": self.url, "timezone": "Asia/Taipei", "intervalMinutes": 10,
                    "startTime": "14:30", "endTime": "23:50"}

    def _update(self, **values):
        with self._lock:
            self._status.update(values)

    def collect_once(self) -> bool:
        self._update(state="updating", lastAttemptAt=self.clock().isoformat(), error=None)
        try:
            request = Request(self.url, headers={"Accept": "application/json",
                              "Cache-Control": "no-cache", "User-Agent": "HanStock-Daily-Pick-Scheduler/1"})
            with self.opener(request, timeout=60) as response:
                payload = json.load(response)
            if payload.get("ok") is not True:
                raise RuntimeError(payload.get("error") or "daily pick refresh failed")
            if payload.get("skipped") is True:
                self._update(state="waiting_for_session", error=None)
                return True
            snapshot = payload.get("snapshot") or {}
            if (payload.get("refreshing") or not snapshot.get("computedAt")
                    or snapshot.get("tradeDate") != self.clock().astimezone(TAIPEI).date().isoformat()
                    or len(snapshot.get("bull", [])) != snapshot.get("bullQualifiedCount")
                    or len(snapshot.get("bear", [])) != snapshot.get("bearQualifiedCount")):
                raise RuntimeError("daily pick snapshot is not complete yet")
            self._update(state="ready", lastSuccessAt=self.clock().isoformat(),
                         tradeDate=snapshot["tradeDate"], computedAt=snapshot["computedAt"],
                         bullCount=len(snapshot["bull"]), bearCount=len(snapshot["bear"]), error=None)
            logger.info("Daily picks saved: %s bull=%d bear=%d", snapshot["tradeDate"],
                        len(snapshot["bull"]), len(snapshot["bear"]))
            return True
        except Exception as exc:
            self._update(state="retrying", error=str(exc)[:300])
            logger.warning("Daily pick refresh will retry: %s", exc)
            return False

    def _run(self):
        # Probe on startup, also catching up immediately after an afternoon redeploy.
        # Before close/on holidays the destination only returns a skip, without a scan.
        while not self._stop.is_set():
            success = self.collect_once()
            now = self.clock()
            target = next_refresh_at(now, retry=not success)
            self._update(nextAttemptAt=target.isoformat())
            if self._stop.wait(max(1, (target - now).total_seconds())):
                break
        self._update(state="stopped", nextAttemptAt=None)

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="hanstock-daily-picks", daemon=True)
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)


_collector = DailyPickCollector(site=os.getenv("HANSTOCK_BATTLE_SITE_URL", SITE))


def start_daily_pick_collector() -> bool:
    if os.getenv("HANSTOCK_DAILY_PICK_COLLECTOR_ENABLED", "true").lower().strip() in {"0", "false", "no", "off"}:
        return False
    return _collector.start()


def stop_daily_pick_collector():
    _collector.stop()


def daily_pick_collector_status() -> dict:
    return _collector.status()
