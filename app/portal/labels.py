"""Human wording for the staff and admin screens.

The database stores `restricted_action`; a support agent reads "Needs authorisation". Every enum
that reaches a screen is translated here, so the wording changes in one place and the templates
stay free of business vocabulary.
"""

from datetime import UTC, datetime

from app.handoff.models import Channel, EscalationReason, Mode, TicketStatus

# label, one-line explanation, severity (drives colour: info | warn | urgent)
REASONS: dict[EscalationReason, tuple[str, str, str]] = {
    EscalationReason.restricted_action: (
        "Needs authorisation",
        "A refund, cancellation, return or payment change. The assistant never does these.",
        "urgent",
    ),
    EscalationReason.customer_requested: (
        "Asked for a person",
        "The customer asked to speak to someone.",
        "info",
    ),
    EscalationReason.negative_sentiment: (
        "Frustrated customer",
        "Two unhappy messages in a row.",
        "urgent",
    ),
    EscalationReason.low_confidence: (
        "Couldn't answer",
        "Nothing in the knowledge base covered the question.",
        "warn",
    ),
    EscalationReason.repeated_failure: (
        "Two misses in a row",
        "The assistant asked to clarify and still could not answer.",
        "warn",
    ),
    EscalationReason.lookup_locked: (
        "Verification locked",
        "Too many failed order checks. This customer needs verifying by hand.",
        "urgent",
    ),
}

CHANNELS: dict[Channel, str] = {
    Channel.webchat: "Web chat",
    Channel.email: "Email",
    Channel.whatsapp: "WhatsApp",
}

MODES: dict[Mode, str] = {
    Mode.bot: "Assistant is handling it",
    Mode.waiting_human: "Waiting for a person",
    Mode.human: "You are handling it",
    Mode.closed: "Closed",
}

STATUSES: dict[TicketStatus, str] = {
    TicketStatus.open: "Open",
    TicketStatus.closed: "Closed",
}

ROLES: dict[str, str] = {"customer": "Customer", "agent": "Assistant", "staff": "You"}


def reason_label(reason: EscalationReason) -> str:
    return REASONS.get(reason, (reason.value, "", "info"))[0]


def reason_help(reason: EscalationReason) -> str:
    return REASONS.get(reason, (reason.value, "", "info"))[1]


def reason_tone(reason: EscalationReason) -> str:
    return REASONS.get(reason, (reason.value, "", "info"))[2]


def channel_label(channel: Channel) -> str:
    return CHANNELS.get(channel, channel.value)


def mode_label(mode: Mode) -> str:
    return MODES.get(mode, mode.value)


def customer_label(handle: str, channel: Channel) -> str:
    """A web visitor has only a session id, which is meaningless on screen."""
    if not handle:
        return "Unknown visitor"
    if channel is Channel.webchat and "@" not in handle:
        return f"Web visitor {handle[-6:]}"
    return handle


def ago(when: datetime, now: datetime | None = None) -> str:
    """Rough relative time. Support staff care about 'how stale', not the exact second."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    seconds = int((now - when).total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 7:
        return f"{days} day{'s' if days != 1 else ''} ago"
    return when.strftime("%d %b")


def register(templates: object) -> None:
    """Expose these helpers to Jinja."""
    env = templates.env  # type: ignore[attr-defined]
    env.globals.update(
        reason_label=reason_label,
        reason_help=reason_help,
        reason_tone=reason_tone,
        channel_label=channel_label,
        mode_label=mode_label,
        customer_label=customer_label,
        ago=ago,
        STATUSES=STATUSES,
        ROLES=ROLES,
    )
