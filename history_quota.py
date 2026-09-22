"""Share a small usage cache so exhausted history requests do not loop on empty data."""
import threading
import time

QUOTA_EXHAUSTED = "history_quota_exhausted: Shioaji 歷史資料流量額度已用完，等待額度恢復"
QUOTA_RESERVED = "history_quota_reserved"


class HistoryQuotaGate:
    def __init__(self):
        self.lock = threading.Lock()
        self.api = None
        self.checked_at = float("-inf")
        self.error = None
        self.last_usage = None
        self.last_probe_at = None

    def check_background(self, api, *, reserve_bytes, now=None):
        """背景回補專用：額度用完之外，剩餘額度低於 reserve_bytes 也回錯誤字串。
        把剩下的額度留給即時需求（開圖的當日 kbars/ticks、櫃買指數歷史、收盤後
        訊號校正），不然全族群 30 天主力回補會在早盤就把一天 500MB 全部燒光。"""
        error = self.check(api, now=now)
        if error:
            return error
        remaining = (self.last_usage or {}).get("remaining_bytes")
        if remaining is None or reserve_bytes <= 0:
            return None
        if float(remaining) < float(reserve_bytes):
            return (
                f"{QUOTA_RESERVED}: 剩餘 {float(remaining) / 1_000_000:.0f} MB，"
                f"低於背景回補保留門檻 {float(reserve_bytes) / 1_000_000:.0f} MB，暫停主力回補"
            )
        return None

    def snapshot(self, api=None):
        """給健康檢查看的額度快照：有 api 就順便（依 TTL）探一次 usage()。"""
        if api is not None:
            self.check(api)
        probed_at = None
        if self.last_probe_at is not None:
            probed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.last_probe_at))
        return {
            "blocked": bool(self.error),
            "error": self.error,
            "usage": dict(self.last_usage) if self.last_usage else None,
            "probedAt": probed_at,
        }

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
                self.last_usage = {
                    key: read(key) for key in ("connections", "bytes", "limit_bytes", "remaining_bytes")
                }
                self.last_probe_at = time.time()
                if remaining is not None and limit is not None:
                    self.error = QUOTA_EXHAUSTED if float(limit) > 0 and float(remaining) <= 0 else None
            except Exception:
                # A failed usage probe must not clear a previously confirmed block.
                pass
            return self.error
        finally:
            self.lock.release()


history_quota = HistoryQuotaGate()
