from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InBoundMessage:
    channel: str
    chat_id: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        # Web UI sessions are stored as full keys like ``web:<hex>``.
        if self.channel == "web":
            return self.chat_id
        return f"{self.channel}:{self.chat_id}"


@dataclass
class OutBoundMessage:
    channel: str
    chat_id: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
