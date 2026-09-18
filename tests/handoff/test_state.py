import pytest
from sqlalchemy.orm import Session

from app.handoff.models import Channel, EscalationReason, Mode, Role, TicketStatus
from app.handoff.service import (
    InvalidTransition,
    conversation_mode,
    create_ticket,
    get_or_create_conversation,
    record_message,
    transition,
)


def _conv(session: Session, cid: str = "c1") -> str:
    get_or_create_conversation(session, cid, Channel.webchat, "widget-123")
    return cid


def test_unknown_conversation_is_bot_mode(session: Session) -> None:
    assert conversation_mode(session, "nope") is Mode.bot


def test_get_or_create_is_idempotent(session: Session) -> None:
    a = get_or_create_conversation(session, "c1", Channel.email, "a@example.com")
    b = get_or_create_conversation(session, "c1", Channel.email, "a@example.com")
    assert a.id == b.id


def test_create_ticket_flips_to_waiting_human(session: Session) -> None:
    cid = _conv(session)
    ticket = create_ticket(
        session, cid, EscalationReason.customer_requested, "Wants a person.", Channel.webchat
    )
    assert ticket.status is TicketStatus.open
    assert conversation_mode(session, cid) is Mode.waiting_human


def test_create_ticket_creates_conversation_if_missing(session: Session) -> None:
    create_ticket(session, "new", EscalationReason.low_confidence, "Unknown q.", Channel.email)
    assert conversation_mode(session, "new") is Mode.waiting_human


def test_second_escalation_returns_existing_open_ticket(session: Session) -> None:
    cid = _conv(session)
    t1 = create_ticket(session, cid, EscalationReason.low_confidence, "s1", Channel.webchat)
    t2 = create_ticket(session, cid, EscalationReason.negative_sentiment, "s2", Channel.webchat)
    assert t1.id == t2.id


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ([Mode.waiting_human, Mode.human, Mode.closed, Mode.bot], True),
        ([Mode.waiting_human, Mode.closed, Mode.bot], True),
        ([Mode.human], False),
        ([Mode.closed], False),
        ([Mode.waiting_human, Mode.bot], False),
        ([Mode.waiting_human, Mode.human, Mode.bot], False),
    ],
)
def test_transitions(session: Session, path: list[Mode], ok: bool) -> None:
    conv = get_or_create_conversation(session, "c1", Channel.webchat, "w")
    if ok:
        for mode in path:
            transition(session, conv, mode)
        assert conv.mode is path[-1]
    else:
        with pytest.raises(InvalidTransition):
            for mode in path:
                transition(session, conv, mode)


def test_customer_message_in_bot_mode_returns_no_ticket(session: Session) -> None:
    cid = _conv(session)
    assert record_message(session, cid, Role.customer, "hello") is None


def test_customer_message_while_waiting_is_attached_to_open_ticket(session: Session) -> None:
    cid = _conv(session)
    ticket = create_ticket(session, cid, EscalationReason.low_confidence, "s", Channel.webchat)
    attached = record_message(session, cid, Role.customer, "any update?")
    assert attached is not None and attached.id == ticket.id
    assert [m.text for m in ticket.conversation.messages][-1] == "any update?"
