"""The admin portal: what the assistant is doing, what it knows, and what it is connected to.

Everything here is read-only except re-indexing the knowledge base. Credentials are never shown
or accepted through the browser — the integrations screen names the variable to set and nothing
more, so a screenshot of this page leaks nothing.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.admin import analytics
from app.agent.core import Agent
from app.agent.routes import get_agent
from app.config import Settings, get_settings
from app.db import get_session
from app.handoff.models import Channel, Ticket, TicketStatus
from app.handoff.routes import require_htmx, require_staff
from app.integrations.base import Status, registry
from app.knowledge_base.ingest import ingest_docs
from app.knowledge_base.models import KbChunk, KbDocument
from app.portal.templates_env import templates
from app.verticals.config import docs_dir

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

router = APIRouter(prefix="/admin", dependencies=[Depends(require_staff)])

TONES = {
    Status.ok: "ok",
    Status.degraded: "warn",
    Status.broken: "urgent",
    Status.not_configured: "grey",
}
STATUS_WORDS = {
    Status.ok: "Connected",
    Status.degraded: "Having trouble",
    Status.broken: "Broken",
    Status.not_configured: "Not set up",
}

# What each integration does for a client, in their words rather than ours.
BLURB = {
    "shopify": "Live order status and product prices straight from the store.",
    "ical_availability": "Which dates a property is free, from its Airbnb or Booking calendar.",
    "whatsapp": "Customers message your WhatsApp Business number and get the same answers.",
    "webchat": "The chat bubble on your website.",
}


def _open_count(session: Session) -> int:
    return int(
        session.scalar(select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.open)) or 0
    )


@router.get("", response_class=HTMLResponse)
def overview(request: Request, session: SessionDep) -> HTMLResponse:
    data = analytics.overview(session)
    return templates.TemplateResponse(
        request,
        "admin_overview.html",
        {"section": "overview", "o": data, "open_count": _open_count(session)},
    )


@router.get("/integrations", response_class=HTMLResponse)
def integrations(request: Request, session: SessionDep, settings: SettingsDep) -> HTMLResponse:
    rows = []

    for name in registry.available:
        connector = registry.get(name)
        if connector is None:
            rows.append(
                {
                    "name": name,
                    "kind": "Connector",
                    "status": Status.not_configured,
                    "detail": "Not switched on for this deployment.",
                    "env": f"CONNECTORS={name}",
                }
            )
            continue
        health = connector.health()
        rows.append(
            {
                "name": name,
                "kind": "Connector",
                "status": health.status,
                "detail": health.detail,
                "env": f"{name.upper()}_*",
            }
        )

    rows.append(
        {
            "name": "webchat",
            "kind": "Channel",
            "status": Status.ok,
            "detail": "Always on. Embed one script tag on the site.",
            "env": "WIDGET_BRAND, WIDGET_ACCENT",
        }
    )
    configured = bool(settings.whatsapp_token and settings.whatsapp_phone_id)
    rows.append(
        {
            "name": "whatsapp",
            "kind": "Channel",
            "status": Status.ok if configured else Status.not_configured,
            "detail": "Receiving and replying."
            if configured
            else "Set the token and phone number id to switch it on.",
            "env": "WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_APP_SECRET",
        }
    )

    model = (
        f"{settings.llm_provider} · {settings.anthropic_model or settings.llm_model}"
        if settings.llm_provider == "anthropic"
        else f"{settings.llm_provider} · {settings.llm_model} at {settings.llm_base_url}"
    )
    return templates.TemplateResponse(
        request,
        "admin_integrations.html",
        {
            "section": "integrations",
            "rows": rows,
            "model": model,
            "blurb": BLURB,
            "tones": TONES,
            "words": STATUS_WORDS,
            "open_count": _open_count(session),
        },
    )


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge(request: Request, session: SessionDep, q: str = "") -> HTMLResponse:
    query = select(KbDocument).options(selectinload(KbDocument.chunks)).order_by(KbDocument.title)
    if q.strip():
        query = query.where(KbDocument.title.ilike(f"%{q.strip()}%"))
    documents = list(session.scalars(query).all())
    total_chunks = int(session.scalar(select(func.count(KbChunk.id))) or 0)
    embedded = int(
        session.scalar(select(func.count(KbChunk.id)).where(KbChunk.embedding.is_not(None))) or 0
    )
    return templates.TemplateResponse(
        request,
        "admin_knowledge.html",
        {
            "section": "knowledge",
            "documents": documents,
            "q": q,
            "total_chunks": total_chunks,
            "embedded": embedded,
            "open_count": _open_count(session),
        },
    )


@router.get("/knowledge/{document_id}", response_class=HTMLResponse)
def document(request: Request, document_id: int, session: SessionDep) -> HTMLResponse:
    doc = session.get(KbDocument, document_id)
    if doc is None:
        return HTMLResponse("Not found", status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "admin_document.html",
        {"section": "knowledge", "doc": doc, "open_count": _open_count(session)},
    )


@router.post("/knowledge/reindex", dependencies=[Depends(require_htmx)])
def reindex(session: SessionDep, settings: SettingsDep) -> HTMLResponse:
    """Re-read the documents folder. Safe to run while the app is serving."""
    stats = ingest_docs(session, docs_dir())
    return HTMLResponse(
        f"Re-indexed {stats.documents_written} documents ({stats.chunks_written} passages).",
        headers={"HX-Trigger": "reindexed"},
    )


@router.get("/playground", response_class=HTMLResponse)
def playground(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin_playground.html",
        {"section": "playground", "open_count": _open_count(session)},
    )


@router.post("/playground", response_class=HTMLResponse)
async def try_it(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    agent: Annotated[Agent, Depends(get_agent)],
    text: Annotated[str, Form()] = "",
) -> HTMLResponse:
    question = text.strip()
    if not question:
        return templates.TemplateResponse(
            request,
            "admin_playground.html",
            {"section": "playground", "open_count": _open_count(session)},
        )

    # Retrieval is shown separately so the gate is visible even when the answer is fine.
    hits = agent.kb.search(question, k=5)
    threshold = (
        settings.kb_min_score
        if getattr(agent.kb, "hybrid", True)
        else settings.kb_min_score_keyword
    )

    # A throwaway conversation: a test here must not appear as a customer in the inbox.
    conversation_id = f"playground-{uuid.uuid4().hex[:10]}"
    error = ""
    reply = None
    try:
        reply = await agent.handle_message(conversation_id, Channel.webchat, question, "playground")
    except Exception as exc:  # noqa: BLE001 - the screen exists to show failures too
        error = f"{exc.__class__.__name__}: {exc}"

    return templates.TemplateResponse(
        request,
        "admin_playground.html",
        {
            "section": "playground",
            "question": question,
            "reply": reply,
            "error": error,
            "hits": hits,
            "threshold": threshold,
            "conversation_id": conversation_id,
            "open_count": _open_count(session),
        },
    )
