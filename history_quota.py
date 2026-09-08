"""Share a small usage cache so exhausted history requests do not loop on empty data."""
import threading
import time

QUOTA_EXHAUSTED = "history_quota_exhausted: Shioaji 歷史資料流量額度已用完，等待額度恢復"


class HistoryQuotaGate:
    def __init__(self):
        self.lock = threading.Lock()
        self.api = None
        self.checked_at = float("-inf")
        self.error = None

    def check(self, api, *, now=None):
        usage = getattr(api, "usage", None)
        if not callable(usage):
            return None
        now = time.monotonic() if now is None else now
        # Never queue chart reads behind another usage query.
        if not self.lock.acquire(blocking=False):
            return self.error or "history_usage_check_pending"
        try:
            if api is not self.api:
                self.api, self.checked_at, self.error = api, float("-inf"), None
            ttl = 300 if self.error else 60
            if 0 <= now - self.checked_at < ttl:
                return self.error
            self.checked_at = now
            try:
                result = usage()
                read = result.get if isinstance(result, dict) else lambda key: getattr(result, key, None)
                remaining, limit = read("remaining_bytes"), read("limit_bytes")
                if remaining is not None and limit is not None:
                    self.error = QUOTA_EXHAUSTED if float(limit) > 0 and float(remaining) <= 0 else None
            except Exception:
                # A failed usage probe must not clear a previously confirmed block.
                pass
            return self.error
        finally:
            self.lock.release()


history_quota = HistoryQuotaGate()
