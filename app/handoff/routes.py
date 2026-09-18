"""Staff UI: ticket queue and ticket view. Server-rendered (Jinja + HTMX), basic-auth."""

import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_session
from app.handoff.models import Ticket, TicketStatus
from app.handoff.sender import OutboundSender, RecordingSender
from app.handoff.service import TicketClosed, close_ticket, staff_reply

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
_basic = HTTPBasic()
_default_sender = RecordingSender()


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


@router.get("", response_class=HTMLResponse)
def queue(request: Request, session: SessionDep) -> HTMLResponse:
    tickets = session.scalars(
        select(Ticket)
        .options(joinedload(Ticket.conversation))
        .where(Ticket.status == TicketStatus.open)
        .order_by(Ticket.created_at, Ticket.id)
    ).all()
    return templates.TemplateResponse(request, "queue.html", {"tickets": tickets})


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_view(request: Request, ticket_id: int, session: SessionDep) -> HTMLResponse:
    ticket = _ticket_or_404(session, ticket_id)
    return templates.TemplateResponse(request, "ticket.html", {"ticket": ticket})


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
