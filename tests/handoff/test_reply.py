import pytest
from sqlalchemy.orm import Session

from app.handoff.models import Channel, EscalationReason, Mode, Role, Ticket, TicketStatus
from app.handoff.sender import RecordingSender
from app.handoff.service import (
    TicketClosed,
    close_ticket,
    conversation_mode,
    create_ticket,
    staff_reply,
)


def _ticket(session: Session) -> Ticket:
    return create_ticket(
        session,
        "c1",
        EscalationReason.restricted_action,
        "Wants to cancel NW-123456.",
        Channel.whatsapp,
        customer_handle="+919800000001",
    )


def test_staff_reply_goes_out_on_original_channel(session: Session) -> None:
    ticket = _ticket(session)
    sender = RecordingSender()
    staff_reply(session, ticket.id, "Hi, I've cancelled it for you.", sender)
    assert sender.sent == [(Channel.whatsapp, "+919800000001", "Hi, I've cancelled it for you.")]


def test_staff_reply_sets_human_mode_and_records_message(session: Session) -> None:
    ticket = _ticket(session)
    staff_reply(session, ticket.id, "On it.", RecordingSender())
    assert conversation_mode(session, "c1") is Mode.human
    last = ticket.conversation.messages[-1]
    assert (last.role, last.text) == (Role.staff, "On it.")


def test_second_staff_reply_keeps_human_mode(session: Session) -> None:
    ticket = _ticket(session)
    staff_reply(session, ticket.id, "one", RecordingSender())
    staff_reply(session, ticket.id, "two", RecordingSender())
    assert conversation_mode(session, "c1") is Mode.human


def test_empty_reply_rejected(session: Session) -> None:
    ticket = _ticket(session)
    with pytest.raises(ValueError, match="empty"):
        staff_reply(session, ticket.id, "   ", RecordingSender())


def test_close_returns_conversation_to_bot(session: Session) -> None:
    ticket = _ticket(session)
    staff_reply(session, ticket.id, "Done.", RecordingSender())
    close_ticket(session, ticket.id)
    assert ticket.status is TicketStatus.closed and ticket.closed_at is not None
    assert conversation_mode(session, "c1") is Mode.bot


def test_close_without_reply(session: Session) -> None:
    ticket = _ticket(session)
    close_ticket(session, ticket.id)
    assert conversation_mode(session, "c1") is Mode.bot


def test_reply_to_closed_ticket_errors_and_sends_nothing(session: Session) -> None:
    ticket = _ticket(session)
    close_ticket(session, ticket.id)
    sender = RecordingSender()
    with pytest.raises(TicketClosed):
        staff_reply(session, ticket.id, "late", sender)
    assert sender.sent == []


def test_close_twice_errors(session: Session) -> None:
    ticket = _ticket(session)
    close_ticket(session, ticket.id)
    with pytest.raises(TicketClosed):
        close_ticket(session, ticket.id)


def test_unknown_ticket(session: Session) -> None:
    with pytest.raises(KeyError):
        staff_reply(session, 999, "hi", RecordingSender())


def test_new_escalation_after_close_opens_new_ticket(session: Session) -> None:
    first = _ticket(session)
    close_ticket(session, first.id)
    second = _ticket(session)
    assert second.id != first.id
