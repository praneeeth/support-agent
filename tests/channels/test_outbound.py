import httpx
import pytest
from sqlalchemy.orm import Session

from app.channels.outbound import ChannelRouter, ChannelUnavailable, WebchatOutbox, build_router
from app.channels.whatsapp import WhatsAppAdapter
from app.config import Settings
from app.handoff.models import Channel, EscalationReason, Mode, Role
from app.handoff.sender import RecordingSender
from app.handoff.service import create_ticket, staff_reply


def test_router_sends_on_the_conversations_own_channel() -> None:
    web, wa = RecordingSender(), RecordingSender()
    router = ChannelRouter({Channel.webchat: web, Channel.whatsapp: wa})
    router.send(Channel.whatsapp, "+15550001", "hello")
    assert wa.sent == [(Channel.whatsapp, "+15550001", "hello")]
    assert web.sent == []


def test_channel_without_a_sender_fails_loudly() -> None:
    router = ChannelRouter({Channel.webchat: WebchatOutbox()})
    with pytest.raises(ChannelUnavailable):
        router.send(Channel.email, "a@example.com", "hello")


def test_unsendable_reply_is_not_recorded(session: Session) -> None:
    """staff_reply sends first, so a channel that can't deliver leaves no phantom message."""
    t = create_ticket(
        session, "c1", EscalationReason.restricted_action, "x", Channel.email, "a@example.com"
    )
    with pytest.raises(ChannelUnavailable):
        staff_reply(session, t.id, "hi", ChannelRouter({}))
    assert [m for m in t.conversation.messages if m.role is Role.staff] == []
    assert t.conversation.mode is Mode.waiting_human


def test_router_offers_whatsapp_only_once_it_can_deliver() -> None:
    """An unconfigured WhatsApp used to accept staff replies and drop them."""
    assert Channel.whatsapp not in build_router(Settings()).senders
    router = build_router(Settings(whatsapp_token="t", whatsapp_phone_id="1"))
    assert isinstance(router.senders[Channel.webchat], WebchatOutbox)
    assert isinstance(router.senders[Channel.whatsapp], WhatsAppAdapter)


def test_a_provider_failure_is_reported_not_recorded() -> None:
    class Down:
        def send(self, channel: Channel, recipient: str, text: str) -> None:
            raise httpx.ConnectError("down")

    with pytest.raises(ChannelUnavailable):
        ChannelRouter({Channel.email: Down()}).send(Channel.email, "a@example.com", "hi")
