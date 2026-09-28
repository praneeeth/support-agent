"""Shared machinery for every channel: normalised inbound message, signature checks, idempotency."""

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import DateTime, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base
from app.handoff.models import Channel

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboundMessage:
    """What every channel turns its payload into."""

    conversation_id: str
    channel: Channel
    text: str
    customer_handle: str
    provider_message_id: str


class ChannelAdapter(Protocol):
    channel: Channel

    def verify(self, body: bytes, headers: dict[str, str]) -> bool: ...
    def parse(self, payload: dict[str, object]) -> list[InboundMessage]: ...
    def send(self, channel: Channel, recipient: str, text: str) -> None: ...


class SeenMessage(Base):
    """One row per provider message id. Providers retry; we answer once."""

    __tablename__ = "seen_messages"

    provider_message_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    channel: Mapped[str] = mapped_column(String(20))
    seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


def claim_message(session: Session, channel: Channel, provider_message_id: str) -> bool:
    """True the first time this id is seen, False on every retry.

    The primary key does the work, so two workers racing on the same retry can't both win.
    """
    if not provider_message_id:
        return True  # nothing to deduplicate on (e.g. the widget); caller handles its own limits
    existing = session.get(SeenMessage, provider_message_id)
    if existing is not None:
        log.info("Duplicate %s message ignored", channel.value)
        return False
    session.add(SeenMessage(provider_message_id=provider_message_id, channel=channel.value))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        log.info("Duplicate %s message ignored (race)", channel.value)
        return False
    return True


def verify_hmac_sha256(
    body: bytes, header_value: str, secret: str, prefix: str = "sha256="
) -> bool:
    """Constant-time check of a provider's HMAC signature (Meta, most email providers)."""
    if not secret or not header_value:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    received = header_value[len(prefix) :] if header_value.startswith(prefix) else header_value
    return hmac.compare_digest(expected, received)


def purge_seen(session: Session, older_than_days: int = 7) -> int:
    """Housekeeping: providers don't retry for a week."""
    cutoff = datetime.now(UTC).replace(tzinfo=None).timestamp() - older_than_days * 86400
    rows = list(session.scalars(select(SeenMessage)))
    removed = 0
    for row in rows:
        if row.seen_at.timestamp() < cutoff:
            session.delete(row)
            removed += 1
    session.commit()
    return removed
