from __future__ import annotations

import asyncio
import json
from typing import Any, TYPE_CHECKING

from fastapi import WebSocket
from loguru import logger

from bus.events import InboundMessage, OutboundMessage
from channels.base import BaseChannel

if TYPE_CHECKING:
    from bus.queue import MessageBus


class WebChannel(BaseChannel):
    """Browser clients: one or more WebSockets per ``chat_id`` (session id)."""

    name = "web"

    def __init__(self, bus: MessageBus) -> None:
        super().__init__(bus)
        self._lock = asyncio.Lock()
        self._sockets: dict[str, set[WebSocket]] = {}

    # -- WebSocket connection management (called by FastAPI routes) ----------

    async def register(self, chat_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._sockets.setdefault(chat_id, set()).add(websocket)

    async def unregister(self, chat_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            s = self._sockets.get(chat_id)
            if not s:
                return
            s.discard(websocket)
            if not s:
                self._sockets.pop(chat_id, None)

    # -- Inbound: parse raw WS frame and publish to bus ----------------------
    async def handle_inbound(self, chat_id: str, raw_text: str) -> None:
        """Parse a raw WebSocket text frame and publish to the inbound queue."""
        try:
            body = json.loads(raw_text)
        except json.JSONDecodeError:
            return
        if body.get("type") != "chat":
            return
        text = str(body.get("message") or "").strip()
        if not text:
            return
        await self.bus.publish_inbound(
            InboundMessage(
                channel="web",
                chat_id=chat_id,
                content=text,
                metadata={"via": "ws"},
            )
        )
    # -- Outbound: push to all registered WS connections ---------------------

    async def send(self, msg: OutboundMessage) -> None:
        kind = msg.metadata.get("kind", "assistant")
        payload: dict[str, Any] = {
            "kind": kind,
            "content": msg.content,
            "channel": msg.channel,
        }
        extra = {k: v for k, v in msg.metadata.items() if k != "kind"}
        if extra:
            payload["metadata"] = extra
        text = json.dumps(payload, ensure_ascii=False)
        async with self._lock:
            targets = list(self._sockets.get(msg.chat_id, ()))
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                logger.debug("WebChannel: send failed", exc_info=True)

    # -- Lifecycle -----------------------------------------------------------

    async def stop(self) -> None:
        async with self._lock:
            for conns in self._sockets.values():
                for ws in list(conns):
                    try:
                        await ws.close()
                    except Exception:
                        logger.debug("WebChannel: close error", exc_info=True)
            self._sockets.clear()
