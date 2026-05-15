from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bus.events import OutboundMessage, InboundMessage
    from bus.queue import MessageBus


class BaseChannel(ABC):
    """Channel 持有 bus 引用, 收发都在内部闭环."""

    def __init__(self, bus: MessageBus) -> None:
        self.bus = bus

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """Deliver an outbound message to this channel's transport."""

    async def start(self) -> None:
        """Optional hook when the channel manager starts."""
        return None

    async def stop(self) -> None:
        """Optional hook when the channel manager stops."""
        return None
