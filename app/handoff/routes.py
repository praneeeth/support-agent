"""The agent portal: the inbox of handed-over conversations, and one conversation in full.

Server-rendered (Jinja + HTMX) behind basic auth. Wording comes from app/portal/labels.py so a
support agent never reads a database enum.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_session
from app.handoff.models import Channel, Conversation, EscalationReason, Ticket, TicketStatus
from app.handoff.sender import OutboundSender, RecordingSender
from app.handoff.service import TicketClosed, close_ticket, staff_reply
from app.portal import labels
from app.portal.templates_env import templates

_basic = HTTPBasic()
_default_sender = RecordingSender()

# Offered above the reply box. Wording a support agent would actually send.
SNIPPETS = [
    {
        "label": "Looking into it",
        "text": "Thanks for your patience — I'm looking into this now and will come back to you "
        "shortly.",
    },
    {
        "label": "Refund approved",
        "text": "I've approved the refund. It goes back to your original payment method and "
        "usually shows within 5–7 working days.",
    },
    {
        "label": "Need more detail",
        "text": "So I can sort this out, could you send me the order number and a photo of the "
        "item as it arrived?",
    },
]


def require_staff(credentials: Annotated[HTTPBasicCredentials, Depends(_basic)]) -> str:
    s = get_settings()
    ok_user = secrets.compare_digest(credentials.username.encode(), s.staff_username.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), s.staff_password.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": 'Basic realm="staff"'}
        )
    return credentials.username


def require_htmx(request: Request) -> None:
    """CSRF guard: browsers can't send a custom header cross-site without a CORS preflight."""
    if request.headers.get("HX-Request") != "true":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing HX-Request header")


def get_sender() -> OutboundSender:
    """Replaced by the channel router once channel modules exist."""
    return _default_sender


SessionDep = Annotated[Session, Depends(get_session)]
router = APIRouter(prefix="/staff", dependencies=[Depends(require_staff)])


def _ticket_or_404(session: Session, ticket_id: int) -> Ticket:
    ticket = session.scalar(
        select(Ticket).options(joinedload(Ticket.conversation)).where(Ticket.id == ticket_id)
    )
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return ticket


def open_count(session: Session) -> int:
    total = session.scalar(select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.open))
    return int(total or 0)


@router.get("", response_class=HTMLResponse)
def queue(
    request: Request,
    session: SessionDep,
    status_filter: Annotated[str, Query(alias="status")] = "open",
    reason: str = "",
    channel: str = "",
    q: str = "",
) -> HTMLResponse:
    if status_filter not in {"open", "closed", "all"}:
        status_filter = "open"

    query = select(Ticket).options(joinedload(Ticket.conversation))
    if status_filter != "all":
        wanted = TicketStatus.open if status_filter == "open" else TicketStatus.closed
        query = query.where(Ticket.status == wanted)
    if reason in EscalationReason.__members__:
        query = query.where(Ticket.reason == EscalationReason[reason])
    if channel in Channel.__members__:
        query = query.join(Ticket.conversation).where(Conversation.channel == Channel[channel])
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.join(Ticket.conversation).where(
            or_(Ticket.summary.ilike(term), Conversation.customer_handle.ilike(term))
        )

    # Open tickets are a work queue: longest wait first. Closed ones are a log: newest first.
    if status_filter == "open":
        query = query.order_by(Ticket.created_at, Ticket.id)
    else:
        query = query.order_by(Ticket.created_at.desc(), Ticket.id.desc())
    tickets = session.scalars(query).all()

    by_status: dict[TicketStatus, int] = {
        row[0]: int(row[1])
        for row in session.execute(
            select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status)
        ).all()
    }
    counts = {
        "open": by_status.get(TicketStatus.open, 0),
        "closed": by_status.get(TicketStatus.closed, 0),
        "all": sum(by_status.values()),
    }
    keep = "".join(
        f"&{name}={value}"
        for name, value in (("reason", reason), ("channel", channel), ("q", q))
        if value
    )
    return templates.TemplateResponse(
        request,
        "queue.html",
        {
            "section": "inbox",
            "tickets": tickets,
            "counts": counts,
            "open_count": counts["open"],
            "status": status_filter,
            "reason": reason,
            "channel": channel,
            "q": q,
            "keep": keep,
            "reason_options": [(r.value, labels.reason_label(r)) for r in EscalationReason],
            "channel_options": [(c.value, labels.channel_label(c)) for c in Channel],
        },
    )


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_view(request: Request, ticket_id: int, session: SessionDep) -> HTMLResponse:
    ticket = _ticket_or_404(session, ticket_id)
    others = [t for t in ticket.conversation.tickets if t.id != ticket.id]
    return templates.TemplateResponse(
        request,
        "ticket.html",
        {
            "section": "inbox",
            "ticket": ticket,
            "other_tickets": others,
            "snippets": SNIPPETS,
            "open_count": open_count(session),
        },
    )


@router.post(
    "/tickets/{ticket_id}/reply",
    response_class=HTMLResponse,
    dependencies=[Depends(require_htmx)],
)
def reply(
    request: Request,
    ticket_id: int,
    session: SessionDep,
    sender: Annotated[OutboundSender, Depends(get_sender)],
    text: Annotated[str, Form()] = "",
) -> HTMLResponse:
    ticket = _ticket_or_404(session, ticket_id)
    try:
        staff_reply(session, ticket.id, text, sender)
    except TicketClosed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket is closed") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return templates.TemplateResponse(request, "_transcript.html", {"ticket": ticket})


@router.post("/tickets/{ticket_id}/close", dependencies=[Depends(require_htmx)])
def close(ticket_id: int, session: SessionDep) -> Response:
    ticket = _ticket_or_404(session, ticket_id)
    try:
        close_ticket(session, ticket.id)
    except TicketClosed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket is already closed") from exc
    return Response(status_code=200, headers={"HX-Redirect": "/staff"})
