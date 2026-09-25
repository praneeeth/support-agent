"""Outbound delivery: a staff reply goes out on the channel the customer wrote in on.

Web chat has nothing to push to — the widget pulls staff replies from the stored transcript
(`GET /chat/messages`), so recording the message is the delivery. WhatsApp pushes through the
Graph API. A channel with no sender raises, so the inbox never shows a reply nobody received.
"""

from dataclasses import dataclass

from app.channels.whatsapp import WhatsAppAdapter
from app.config import Settings
from app.handoff.models import Channel
from app.handoff.sender import OutboundSender


class ChannelUnavailable(RuntimeError):
    """No way to reach a customer on this channel yet."""


class WebchatOutbox:
    """Pull-based: `staff_reply` records the message after sending, and the widget polls it."""

    def send(self, channel: Channel, recipient: str, text: str) -> None:
        return None


@dataclass
class ChannelRouter:
    senders: dict[Channel, OutboundSender]

    def send(self, channel: Channel, recipient: str, text: str) -> None:
        sender = self.senders.get(channel)
        if sender is None:
            raise ChannelUnavailable(channel.value)
        sender.send(channel, recipient, text)


def build_router(settings: Settings) -> ChannelRouter:
    return ChannelRouter(
        {Channel.webchat: WebchatOutbox(), Channel.whatsapp: WhatsAppAdapter(settings)}
    )
