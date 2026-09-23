"""Web chat: the embeddable widget and the endpoint it talks to.

A site embeds one script tag. The widget keeps a session id in the browser, posts messages here
and renders replies. Escalations look different from answers, so the customer knows a person
is coming.
"""

import secrets
import time
from collections import defaultdict
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.core import Agent
from app.agent.routes import get_agent
from app.config import Settings, get_settings
from app.db import get_session
from app.handoff.models import Channel

router = APIRouter(prefix="/chat")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

WIDGET_JS = (Path(__file__).parent / "static" / "widget.js").read_text(encoding="utf-8")

# Per-session sliding window. In-process is fine for one instance per client.
_RATE: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT = 20  # messages
RATE_WINDOW = 60.0  # seconds


class ChatIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    text: str = Field(min_length=1, max_length=2000)


class ChatOut(BaseModel):
    kind: str
    text: str
    sources: list[str] = []
    handed_off: bool = False


def _rate_limited(session_id: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _RATE[session_id] if now - t < RATE_WINDOW]
    hits.append(now)
    _RATE[session_id] = hits
    return len(hits) > RATE_LIMIT


@router.get("/widget.js")
def widget_js(settings: Annotated[Settings, Depends(get_settings)]) -> Response:
    """The embed script. One <script src="…/chat/widget.js" defer></script> on the client's site."""
    body = WIDGET_JS.replace("__BRAND__", settings.widget_brand).replace(
        "__GREETING__", settings.widget_greeting
    )
    return Response(
        body,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"},
    )


@router.post("/message", response_model=ChatOut)
async def message(
    body: ChatIn,
    agent: Annotated[Agent, Depends(get_agent)],
) -> ChatOut:
    if _rate_limited(body.session_id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many messages, slow down.")
    reply = await agent.handle_message(
        conversation_id=f"web-{body.session_id}",
        channel=Channel.webchat,
        text=body.text,
        customer_handle=body.session_id,
    )
    return ChatOut(
        kind=reply.kind,
        text=reply.text or "A member of our team will reply here shortly.",
        sources=list(reply.sources),
        handed_off=reply.kind == "escalated",
    )


@router.get("/demo", response_class=HTMLResponse)
def demo(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    """A stand-in for the client's website, with the widget on it. This is the sales demo."""
    return templates.TemplateResponse(
        request,
        "demo.html",
        {"brand": settings.widget_brand, "nonce": secrets.token_hex(4)},
    )
