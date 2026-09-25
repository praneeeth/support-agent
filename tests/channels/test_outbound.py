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


def test_default_router_covers_webchat_and_whatsapp() -> None:
    router = build_router(Settings())
    assert isinstance(router.senders[Channel.webchat], WebchatOutbox)
    assert isinstance(router.senders[Channel.whatsapp], WhatsAppAdapter)
    assert Channel.email not in router.senders
