import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Channel(enum.StrEnum):
    webchat = "webchat"
    email = "email"
    whatsapp = "whatsapp"


class Mode(enum.StrEnum):
    bot = "bot"
    waiting_human = "waiting_human"
    human = "human"
    closed = "closed"


class Role(enum.StrEnum):
    customer = "customer"
    agent = "agent"
    staff = "staff"


class EscalationReason(enum.StrEnum):
    low_confidence = "low_confidence"
    customer_requested = "customer_requested"
    negative_sentiment = "negative_sentiment"
    restricted_action = "restricted_action"
    repeated_failure = "repeated_failure"
    lookup_locked = "lookup_locked"


class TicketStatus(enum.StrEnum):
    open = "open"
    closed = "closed"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[Channel] = mapped_column(Enum(Channel))
    customer_handle: Mapped[str] = mapped_column(String(200))  # email, phone or widget session
    mode: Mapped[Mode] = mapped_column(Enum(Mode), default=Mode.bot)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.id", cascade="all, delete-orphan"
    )
    tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="conversation", order_by="Ticket.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[Role] = mapped_column(Enum(Role))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    reason: Mapped[EscalationReason] = mapped_column(Enum(EscalationReason))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.open)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="tickets")
