"""Email over Postmark. Works the moment the keys are set; inert until then.

Postmark POSTs each inbound email as JSON to a webhook URL. Postmark doesn't sign inbound
webhooks; its documented protection is HTTP Basic auth on the URL, so the URL is configured as
https://user:password@host/channels/email and anything without those credentials gets a 401.
Replies go out through the Postmark API with In-Reply-To/References set, so they thread.

One conversation per sender address, as WhatsApp has one per phone number: a customer who
replies, or writes a fresh email, reaches the same conversation and the same open ticket.
"""

import base64
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.agent.core import Agent
from app.agent.routes import get_agent
from app.channels.base import InboundMessage, claim_message
from app.config import Settings, get_settings
from app.db import get_session
from app.handoff.models import Channel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/channels/email")

API = "https://api.postmarkapp.com/email"
DEFAULT_SUBJECT = "Your message"

# Senders that are machines. Answering them starts a mail loop.
_MACHINE = re.compile(r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply)@", re.IGNORECASE)
# Where quoted history starts in the common mail clients.
_QUOTE_START = re.compile(
    r"^(On .+ wrote:\s*$|-{2,}\s*Original Message\s*-{2,}|From: .+|_{5,}|>)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EmailMessage:
    inbound: InboundMessage
    subject: str
    message_id_header: str  # the RFC Message-ID, for threading the reply


def conversation_id_for(address: str) -> str:
    """Stable, short and not the address itself (conversation ids appear in URLs and logs)."""
    digest = hashlib.sha256(address.strip().lower().encode()).hexdigest()[:24]
    return f"em-{digest}"


def strip_quoted(text: str) -> str:
    """Keep what the customer just wrote; drop the thread they replied to."""
    kept: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if _QUOTE_START.match(line.strip()):
            break
        kept.append(line)
    return "\n".join(kept).strip()


class EmailAdapter:
    channel = Channel.email

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.settings.postmark_server_token and self.settings.postmark_from)

    def verify(self, headers: dict[str, str]) -> bool:
        """Basic auth on the webhook URL. With no credentials configured, nothing gets in."""
        user, password = (
            self.settings.postmark_inbound_user,
            self.settings.postmark_inbound_password,
        )
        if not user or not password:
            return False
        expected = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        return secrets.compare_digest(headers.get("authorization", "").encode(), expected.encode())

    def parse(self, payload: dict[str, Any]) -> list[EmailMessage]:
        headers = {
            str(h.get("Name", "")).lower(): str(h.get("Value", ""))
            for h in payload.get("Headers") or []
        }
        if headers.get("auto-submitted", "no").lower() != "no":
            return []
        if headers.get("precedence", "").lower() in {"bulk", "junk", "list", "auto_reply"}:
            return []
        sender = str((payload.get("FromFull") or {}).get("Email") or payload.get("From") or "")
        sender = sender.strip().lower()
        own = self.settings.postmark_from.strip().lower()
        if not sender or _MACHINE.match(sender) or (own and sender == own):
            return []
        text = str(payload.get("StrippedTextReply") or "").strip() or strip_quoted(
            str(payload.get("TextBody") or "")
        )
        if not text:
            return []
        return [
            EmailMessage(
                inbound=InboundMessage(
                    conversation_id=conversation_id_for(sender),
                    channel=Channel.email,
                    text=text[:4000],
                    customer_handle=sender,
                    provider_message_id=str(payload.get("MessageID", "")),
                ),
                subject=str(payload.get("Subject") or "").strip(),
                message_id_header=headers.get("message-id", ""),
            )
        ]

    def send(self, channel: Channel, recipient: str, text: str) -> None:
        """A staff reply, which has no inbound email to thread onto."""
        self.reply(recipient, text, subject="", in_reply_to="")

    def reply(self, recipient: str, text: str, subject: str, in_reply_to: str) -> None:
        if not self.configured:
            log.warning("Email not configured; reply dropped")
            return
        subject = subject or DEFAULT_SUBJECT
        body: dict[str, Any] = {
            "From": self.settings.postmark_from,
            "To": recipient,
            "Subject": subject if subject.lower().startswith("re:") else f"Re: {subject}",
            "TextBody": text,
            "MessageStream": "outbound",
        }
        if in_reply_to:
            body["Headers"] = [
                {"Name": "In-Reply-To", "Value": in_reply_to},
                {"Name": "References", "Value": in_reply_to},
            ]
        client = self._client or httpx.Client(timeout=10.0)
        try:
            response = client.post(
                API,
                headers={
                    "X-Postmark-Server-Token": self.settings.postmark_server_token,
                    "Accept": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Postmark send failed: %s", exc.__class__.__name__)
            raise


def get_adapter(settings: Annotated[Settings, Depends(get_settings)]) -> EmailAdapter:
    return EmailAdapter(settings)


@router.post("")
async def receive(
    request: Request,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
    agent: Annotated[Agent, Depends(get_agent)],
    adapter: Annotated[EmailAdapter, Depends(get_adapter)],
) -> Response:
    """Verify, deduplicate, acknowledge fast, answer in the background (Postmark retries)."""
    if not adapter.verify({k.lower(): v for k, v in request.headers.items()}):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": 'Basic realm="email"'}
        )
    for message in adapter.parse(await request.json()):
        if not claim_message(session, Channel.email, message.inbound.provider_message_id):
            continue
        background.add_task(_handle, agent, adapter, message)
    return Response(status_code=status.HTTP_200_OK)


async def _handle(agent: Agent, adapter: EmailAdapter, message: EmailMessage) -> None:
    m = message.inbound
    reply = await agent.handle_message(m.conversation_id, m.channel, m.text, m.customer_handle)
    if reply.kind != "silent" and reply.text:
        try:
            adapter.reply(m.customer_handle, reply.text, message.subject, message.message_id_header)
        except httpx.HTTPError:
            # The conversation and any ticket are recorded; a person can follow up from there.
            log.warning("Email reply not delivered in %s", m.conversation_id)
