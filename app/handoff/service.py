"""Escalation tickets and conversation mode (who is allowed to reply)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.handoff.models import (
    Channel,
    Conversation,
    EscalationReason,
    Message,
    Mode,
    Role,
    Ticket,
    TicketStatus,
    _now,
)
from app.handoff.sender import OutboundSender


class InvalidTransition(ValueError):
    pass


class TicketClosed(ValueError):
    pass


_ALLOWED: dict[Mode, set[Mode]] = {
    Mode.bot: {Mode.waiting_human},
    Mode.waiting_human: {Mode.human, Mode.closed},
    Mode.human: {Mode.closed},
    Mode.closed: {Mode.bot},
}


def transition(session: Session, conv: Conversation, new: Mode) -> None:
    if new not in _ALLOWED[conv.mode]:
        raise InvalidTransition(f"{conv.mode} -> {new}")
    conv.mode = new
    session.flush()


def get_or_create_conversation(
    session: Session, conversation_id: str, channel: Channel, customer_handle: str
) -> Conversation:
    conv = session.get(Conversation, conversation_id)
    if conv is None:
        conv = Conversation(
            id=conversation_id, channel=channel, customer_handle=customer_handle, mode=Mode.bot
        )
        session.add(conv)
        session.commit()
    return conv


def conversation_mode(session: Session, conversation_id: str) -> Mode:
    conv = session.get(Conversation, conversation_id)
    return conv.mode if conv is not None else Mode.bot


def open_ticket(session: Session, conversation_id: str) -> Ticket | None:
    return session.scalar(
        select(Ticket).where(
            Ticket.conversation_id == conversation_id, Ticket.status == TicketStatus.open
        )
    )


def create_ticket(
    session: Session,
    conversation_id: str,
    reason: EscalationReason,
    summary: str,
    channel: Channel,
    customer_handle: str = "",
) -> Ticket:
    """Escalate to a human. Returns the already-open ticket if there is one."""
    existing = open_ticket(session, conversation_id)
    if existing is not None:
        return existing
    conv = get_or_create_conversation(session, conversation_id, channel, customer_handle)
    transition(session, conv, Mode.waiting_human)
    ticket = Ticket(conversation=conv, reason=reason, summary=summary.strip())
    session.add(ticket)
    session.commit()
    return ticket


def record_message(session: Session, conversation_id: str, role: Role, text: str) -> Ticket | None:
    """Append a message to the transcript. Returns the open ticket if a human owns the chat."""
    conv = session.get(Conversation, conversation_id)
    if conv is None:
        raise KeyError(conversation_id)
    session.add(Message(conversation=conv, role=role, text=text))
    session.commit()
    return open_ticket(session, conversation_id) if conv.mode is not Mode.bot else None


def _get_ticket(session: Session, ticket_id: int) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise KeyError(ticket_id)
    return ticket


def staff_reply(session: Session, ticket_id: int, text: str, sender: OutboundSender) -> None:
    """Send a staff message to the customer on the conversation's own channel."""
    text = text.strip()
    if not text:
        raise ValueError("Reply is empty")
    ticket = _get_ticket(session, ticket_id)
    if ticket.status is TicketStatus.closed:
        raise TicketClosed(ticket_id)
    conv = ticket.conversation
    sender.send(conv.channel, conv.customer_handle, text)  # send first: record only what went out
    if conv.mode is Mode.waiting_human:
        transition(session, conv, Mode.human)
    session.add(Message(conversation=conv, role=Role.staff, text=text))
    session.commit()


def close_ticket(session: Session, ticket_id: int) -> None:
    """Close the ticket and hand the conversation back to the bot."""
    ticket = _get_ticket(session, ticket_id)
    if ticket.status is TicketStatus.closed:
        raise TicketClosed(ticket_id)
    ticket.status = TicketStatus.closed
    ticket.closed_at = _now()
    conv = ticket.conversation
    transition(session, conv, Mode.closed)
    transition(session, conv, Mode.bot)
    session.commit()
