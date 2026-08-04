"""三層自動重連監控模組。

Layer 1: Shioaji ↔ Backend（由 QuoteService._reconnect_loop 處理）
Layer 2: Backend WebSocket ↔ Browser（由 ws_server.py heartbeat 處理）
Layer 3: Browser WebSocket 斷線重連（由前端 useWebSocketMarket hook 處理）

本模組提供：
1. 重連狀態統一查詢 API
2. Shioaji 連線健康監控（定期檢查 quote_stale → 主動觸發重連）
3. 連線事件日誌
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("hanstock.reconnect_monitor")

TW_TZ = timezone(timedelta(hours=8))

# 若 Shioaji 行情超過此秒數無更新，視為斷線並觸發重連
STALE_THRESHOLD_SECONDS = 120
# 健康檢查間隔
HEALTH_CHECK_INTERVAL = 30
# 事件日誌最大保留數
MAX_EVENT_LOG = 200


@dataclass
class ReconnectEvent:
    """重連事件紀錄。"""
    layer: str  # "shioaji" | "ws_server" | "browser"
    event: str  # "disconnected" | "reconnecting" | "reconnected" | "failed"
    timestamp: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "layer": self.layer,
            "event": self.event,
            "timestamp": self.timestamp,
            "detail": self.detail,
        }


class ReconnectMonitor:
    """三層重連狀態監控。"""

    def __init__(self) -> None:
        self._events: deque[ReconnectEvent] = deque(maxlen=MAX_EVENT_LOG)
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()
        self._shioaji_reconnect_count = 0
        self._ws_reconnect_count = 0

    def start(self) -> None:
        """啟動背景健康監控執行緒。"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._shutdown.clear()
        self._monitor_thread = threading.Thread(
            target=self._health_check_loop,
            name="reconnect-monitor",
            daemon=True,
        )
        self._monitor_thread.start()
        logger.info("[ReconnectMonitor] 健康監控已啟動")

    def stop(self) -> None:
        self._shutdown.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)

    def log_event(self, layer: str, event: str, detail: str = "") -> None:
        """記錄重連事件。"""
        now = datetime.now(TW_TZ).isoformat()
        entry = ReconnectEvent(layer=layer, event=event, timestamp=now, detail=detail)
        with self._lock:
            self._events.append(entry)
            if layer == "shioaji" and event == "reconnected":
                self._shioaji_reconnect_count += 1
            elif layer == "ws_server" and event == "reconnected":
                self._ws_reconnect_count += 1
        logger.info("[ReconnectMonitor] %s/%s: %s", layer, event, detail)

    def get_status(self) -> dict[str, Any]:
        """取得三層重連狀態摘要。"""
        from quote_service import get_quote_service
        from market_data_hub import get_market_data_hub

        svc = get_quote_service()
        hub = get_market_data_hub()

        # Layer 1: Shioaji
        health = svc.get_health()
        shioaji_status = "connected" if health.get("quote_connected") else "disconnected"
        if health.get("quote_stale"):
            shioaji_status = "stale"

        # Layer 2: WebSocket Server
        hub_status = hub.get_hub_status()
        ws_clients = hub_status.get("websocket_clients", 0)

        with self._lock:
            recent_events = [e.to_dict() for e in list(self._events)[-20:]]

        return {
            "layers": {
                "shioaji_backend": {
                    "status": shioaji_status,
                    "quote_connected": health.get("quote_connected"),
                    "quote_stale": health.get("quote_stale"),
                    "quote_age_seconds": health.get("quote_age_seconds"),
                    "reconnect_count": health.get("reconnect_count", 0),
                    "last_event": health.get("last_event"),
                },
                "backend_websocket": {
                    "status": "active" if ws_clients > 0 else "idle",
                    "connected_clients": ws_clients,
                    "total_reconnects": self._ws_reconnect_count,
                },
                "browser_websocket": {
                    "status": "managed_by_frontend",
                    "note": "Browser reconnect handled by useWebSocketMarket hook (exponential backoff, max 10 attempts)",
                },
            },
            "recent_events": recent_events,
            "total_shioaji_reconnects": self._shioaji_reconnect_count,
        }

    def _health_check_loop(self) -> None:
        """定期檢查 Shioaji 連線健康，必要時觸發重連。"""
        while not self._shutdown.wait(timeout=HEALTH_CHECK_INTERVAL):
            try:
                from quote_service import get_quote_service
                svc = get_quote_service()
                health = svc.get_health()

                age = health.get("quote_age_seconds")
                if age is not None and age > STALE_THRESHOLD_SECONDS:
                    if svc.state.quote_connected:
                        self.log_event(
                            "shioaji", "stale_detected",
                            f"行情已 {age:.0f} 秒未更新，觸發重連"
                        )
                        svc.state.quote_connected = False
                        svc._trigger_reconnect()
            except Exception as exc:
                logger.debug("[ReconnectMonitor] 健康檢查異常: %s", exc)


# 全域單例
_monitor: Optional[ReconnectMonitor] = None


def get_reconnect_monitor() -> ReconnectMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ReconnectMonitor()
    return _monitor
