"""HanStock WebSocket Server — 即時行情推送。

提供 /ws/market WebSocket 端點，前端連線後即時接收：
- tick: 個股即時 tick
- futures_tick: 台指期即時 tick
- bar_completed: 5 分 K 完成通知

客戶端可發送訂閱指令控制接收範圍：
- {"action": "subscribe", "codes": ["2330", "2317"]}
- {"action": "unsubscribe", "codes": ["2330"]}
- {"action": "subscribe_all"}  (接收所有 tick)
- {"action": "ping"}  → 回傳 {"type": "pong"}

三層自動重連中的第二層（Backend WebSocket ↔ Browser）由此模組提供。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

from market_data_hub import get_market_data_hub

logger = logging.getLogger("hanstock.ws_server")

# Heartbeat interval (seconds) - server sends ping to keep connection alive
WS_HEARTBEAT_INTERVAL = 30
# Maximum time without client pong before disconnecting
WS_PONG_TIMEOUT = 60


class WSClient:
    """代表一個 WebSocket 連線客戶端。"""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.subscribed_codes: set[str] = set()
        self.subscribe_all: bool = False
        self.connected_at: float = time.time()
        self.last_pong: float = time.time()
        self.messages_sent: int = 0

    def should_receive(self, message: dict[str, Any]) -> bool:
        """判斷此客戶端是否應接收此訊息。"""
        if self.subscribe_all:
            return True
        msg_type = message.get("type", "")
        if msg_type == "futures_tick":
            # 期貨 tick 預設所有人都收
            return True
        code = message.get("code", "")
        return code in self.subscribed_codes


async def websocket_endpoint(websocket: WebSocket) -> None:
    """FastAPI WebSocket 端點處理函式。"""
    await websocket.accept()
    hub = get_market_data_hub()
    queue = hub.subscribe_ws()
    client = WSClient(websocket)

    logger.info("[WS] 新客戶端連線（目前 %d 人）", len(hub._ws_subscribers))

    # 發送歡迎訊息
    try:
        await websocket.send_json({
            "type": "connected",
            "hub_status": hub.get_hub_status(),
            "message": "HanStock Market Data Hub WebSocket 已連線",
        })
    except Exception:
        hub.unsubscribe_ws(queue)
        return

    # 並行任務：接收客戶端指令 + 推送行情 + 心跳
    send_task = asyncio.create_task(_send_loop(client, queue, hub))
    recv_task = asyncio.create_task(_recv_loop(client, hub))
    heartbeat_task = asyncio.create_task(_heartbeat_loop(client))

    try:
        done, pending = await asyncio.wait(
            [send_task, recv_task, heartbeat_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except Exception as exc:
        logger.warning("[WS] 連線異常: %s", exc)
    finally:
        hub.unsubscribe_ws(queue)
        logger.info("[WS] 客戶端斷線（已送 %d 訊息）", client.messages_sent)


async def _send_loop(client: WSClient, queue: asyncio.Queue, hub: Any) -> None:
    """從 Hub queue 取出訊息並推送到客戶端。"""
    try:
        while True:
            message = await queue.get()
            if not client.should_receive(message):
                continue
            try:
                await client.websocket.send_json(message)
                client.messages_sent += 1
            except Exception:
                return
    except asyncio.CancelledError:
        pass


async def _recv_loop(client: WSClient, hub: Any) -> None:
    """接收客戶端指令（subscribe/unsubscribe/ping）。"""
    try:
        while True:
            raw = await client.websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await client.websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON",
                })
                continue

            action = msg.get("action", "")

            if action == "ping":
                client.last_pong = time.time()
                await client.websocket.send_json({"type": "pong", "ts": int(time.time() * 1000)})

            elif action == "subscribe":
                codes = msg.get("codes", [])
                if isinstance(codes, list):
                    new_codes = {str(c).strip().upper() for c in codes if c}
                    client.subscribed_codes.update(new_codes)
                    # 同時確保 Shioaji 訂閱這些股票
                    from quote_service import get_quote_service
                    svc = get_quote_service()
                    if svc.state.logged_in:
                        svc.ensure_stock_subscriptions(list(new_codes))
                    await client.websocket.send_json({
                        "type": "subscribed",
                        "codes": sorted(client.subscribed_codes),
                        "subscribe_all": client.subscribe_all,
                    })

            elif action == "unsubscribe":
                codes = msg.get("codes", [])
                if isinstance(codes, list):
                    for c in codes:
                        client.subscribed_codes.discard(str(c).strip().upper())
                    await client.websocket.send_json({
                        "type": "subscribed",
                        "codes": sorted(client.subscribed_codes),
                        "subscribe_all": client.subscribe_all,
                    })

            elif action == "subscribe_all":
                client.subscribe_all = True
                await client.websocket.send_json({
                    "type": "subscribed",
                    "codes": sorted(client.subscribed_codes),
                    "subscribe_all": True,
                })

            elif action == "get_bars":
                code = str(msg.get("code", "")).strip().upper()
                if code:
                    bars = hub.get_live_bars(code)
                    await client.websocket.send_json({
                        "type": "bars",
                        "code": code,
                        "bars": bars,
                    })

            elif action == "get_status":
                await client.websocket.send_json({
                    "type": "hub_status",
                    "data": hub.get_hub_status(),
                })

            else:
                await client.websocket.send_json({
                    "type": "error",
                    "message": f"Unknown action: {action}",
                })

    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("[WS] recv_loop 異常: %s", exc)


async def _heartbeat_loop(client: WSClient) -> None:
    """定期發送 heartbeat，確保連線存活。"""
    try:
        while True:
            await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
            try:
                await client.websocket.send_json({
                    "type": "heartbeat",
                    "ts": int(time.time() * 1000),
                    "messages_sent": client.messages_sent,
                })
            except Exception:
                return
            # 檢查 pong timeout
            if time.time() - client.last_pong > WS_PONG_TIMEOUT:
                logger.warning("[WS] 客戶端 pong 超時，斷開連線")
                return
    except asyncio.CancelledError:
        pass
