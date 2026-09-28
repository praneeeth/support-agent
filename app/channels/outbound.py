"""Outbound delivery: a staff reply goes out on the channel the customer wrote in on.

Web chat has nothing to push to — the widget pulls staff replies from the stored transcript
(`GET /chat/messages`), so recording the message is the delivery. WhatsApp and email push through
their providers, and are offered only once configured. A channel that can't deliver raises, so the
inbox never shows a reply nobody received.
"""

from dataclasses import dataclass

import httpx

from app.channels.email import EmailAdapter
from app.channels.whatsapp import WhatsAppAdapter
from app.config import Settings
from app.handoff.models import Channel
from app.handoff.sender import OutboundSender


class ChannelUnavailable(RuntimeError):
    """No way to reach a customer on this channel right now."""


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
        try:
            sender.send(channel, recipient, text)
        except httpx.HTTPError as exc:  # the provider refused or timed out
            raise ChannelUnavailable(channel.value) from exc


def build_router(settings: Settings) -> ChannelRouter:
    senders: dict[Channel, OutboundSender] = {Channel.webchat: WebchatOutbox()}
    whatsapp = WhatsAppAdapter(settings)
    if whatsapp.configured:
        senders[Channel.whatsapp] = whatsapp
    email = EmailAdapter(settings)
    if email.configured:
        senders[Channel.email] = email
    return ChannelRouter(senders)
