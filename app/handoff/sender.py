"""Outbound delivery abstraction. Each channel module provides an implementation."""

from dataclasses import dataclass, field
from typing import Protocol

from app.handoff.models import Channel


class OutboundSender(Protocol):
    def send(self, channel: Channel, recipient: str, text: str) -> None: ...


@dataclass
class RecordingSender:
    """Stores messages instead of sending them. Used in tests and local demos."""

    sent: list[tuple[Channel, str, str]] = field(default_factory=list)

    def send(self, channel: Channel, recipient: str, text: str) -> None:
        self.sent.append((channel, recipient, text))
