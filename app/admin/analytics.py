"""Read-only counts for the overview screen.

Everything here comes from the tables the app already writes. There is no separate telemetry
pipeline, so these numbers cannot disagree with what the inbox shows.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.handoff.models import Conversation, EscalationReason, Message, Role, Ticket, TicketStatus

WINDOW_DAYS = 14


@dataclass(frozen=True)
class Day:
    day: date
    handled: int
    escalated: int

    @property
    def total(self) -> int:
        return self.handled + self.escalated


@dataclass
class Overview:
    conversations: int = 0
    escalated: int = 0
    tickets_open: int = 0
    customer_messages: int = 0
    days: list[Day] = field(default_factory=list)
    reasons: list[tuple[EscalationReason, int]] = field(default_factory=list)
    unanswered: list[Ticket] = field(default_factory=list)

    @property
    def handled(self) -> int:
        return self.conversations - self.escalated

    @property
    def handled_pct(self) -> int:
        if not self.conversations:
            return 0
        return round(100 * self.handled / self.conversations)

    @property
    def busiest(self) -> int:
        return max((d.total for d in self.days), default=0)


# The two reasons that mean the knowledge base fell short — the ones worth fixing.
GAP_REASONS = (EscalationReason.low_confidence, EscalationReason.repeated_failure)


def overview(session: Session, today: date | None = None) -> Overview:
    today = today or datetime.now(UTC).date()
    out = Overview()

    out.conversations = int(session.scalar(select(func.count(Conversation.id))) or 0)
    out.tickets_open = int(
        session.scalar(select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.open)) or 0
    )
    out.customer_messages = int(
        session.scalar(select(func.count(Message.id)).where(Message.role == Role.customer)) or 0
    )

    # A conversation counts as escalated once, however many tickets it produced.
    escalated_ids = set(session.scalars(select(Ticket.conversation_id).distinct()).all())
    out.escalated = len(escalated_ids)

    started: Counter[date] = Counter()
    escalated_on: Counter[date] = Counter()
    for conversation_id, created in session.execute(
        select(Conversation.id, Conversation.created_at)
    ).all():
        day = created.date()
        started[day] += 1
        if conversation_id in escalated_ids:
            escalated_on[day] += 1

    out.days = [
        Day(
            day=today - timedelta(days=offset),
            handled=started[today - timedelta(days=offset)]
            - escalated_on[today - timedelta(days=offset)],
            escalated=escalated_on[today - timedelta(days=offset)],
        )
        for offset in range(WINDOW_DAYS - 1, -1, -1)
    ]

    counts = session.execute(
        select(Ticket.reason, func.count(Ticket.id)).group_by(Ticket.reason)
    ).all()
    out.reasons = sorted(
        ((reason, int(n)) for reason, n in counts), key=lambda pair: pair[1], reverse=True
    )

    out.unanswered = list(
        session.scalars(
            select(Ticket)
            .where(Ticket.reason.in_(GAP_REASONS))
            .order_by(Ticket.created_at.desc())
            .limit(8)
        ).all()
    )
    return out
