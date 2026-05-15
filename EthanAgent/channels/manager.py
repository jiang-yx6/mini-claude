"""Channel manager: dispatch outbound messages to registered channels."""

from __future__ import annotations

import asyncio

from loguru import logger

from bus.events import OutboundMessage
from bus.queue import MessageBus
from channels.base import BaseChannel


class ChannelManager:
    """Manage channel lifecycle and route outbound messages.

    Responsibilities:
    - Hold registered channels
    - Dispatch outbound messages from ``bus.outbound`` to the matching channel
    - Start/stop all channels
    """

    def __init__(self, bus: MessageBus) -> None:
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    def register(self, name: str, channel: BaseChannel) -> None:
        self.channels[name] = channel
        logger.info("ChannelManager: registered channel '{}'", name)

    async def _dispatch_outbound(self) -> None:
        logger.info("ChannelManager: outbound dispatcher started")
        while not self._stopped.is_set():
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_outbound(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            ch = self.channels.get(msg.channel)
            if ch is None:
                logger.warning("ChannelManager: unknown channel '{}'", msg.channel)
                continue
            try:
                await ch.send(msg)
            except Exception:
                logger.exception(
                    "ChannelManager: send failed channel='{}'", msg.channel
                )
        logger.info("ChannelManager: outbound dispatcher stopped")

    async def start(self) -> None:
        if self._dispatch_task is not None:
            return
        self._stopped.clear()
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

    async def stop(self) -> None:
        self._stopped.set()
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None
        for name, ch in self.channels.items():
            try:
                await ch.stop()
            except Exception:
                logger.exception(
                    "ChannelManager: channel stop failed name='{}'", name
                )
