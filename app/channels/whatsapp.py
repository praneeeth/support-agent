"""WhatsApp Cloud API channel. Works the moment a token is configured; inert until then.

Meta's flow: they GET the webhook once to verify it, then POST every inbound message, signed with
the app secret. Replies go to the Graph API. Outside the 24-hour customer-service window only
approved templates may be sent — we don't send templates, so outside it we stay silent and the
ticket waits for a human.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.agent.core import Agent
from app.agent.routes import get_agent
from app.channels.base import InboundMessage, claim_message, verify_hmac_sha256
from app.config import Settings, get_settings
from app.db import get_session
from app.handoff.models import Channel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/channels/whatsapp")

GRAPH = "https://graph.facebook.com/v21.0"
WINDOW = timedelta(hours=24)


class WhatsAppAdapter:
    channel = Channel.whatsapp

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.settings.whatsapp_token and self.settings.whatsapp_phone_id)

    def verify(self, body: bytes, headers: dict[str, str]) -> bool:
        signature = headers.get("x-hub-signature-256", "")
        return verify_hmac_sha256(body, signature, self.settings.whatsapp_app_secret)

    def parse(self, payload: dict[str, Any]) -> list[InboundMessage]:
        """Meta nests messages three levels deep and batches them."""
        out: list[InboundMessage] = []
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value") or {}
                for message in value.get("messages", []) or []:
                    if message.get("type") != "text":
                        # images, audio, location: left for a human rather than guessed at
                        continue
                    sender = str(message.get("from", ""))
                    text = ((message.get("text") or {}).get("body") or "").strip()
                    if not sender or not text:
                        continue
                    out.append(
                        InboundMessage(
                            conversation_id=f"wa-{sender}",
                            channel=Channel.whatsapp,
                            text=text,
                            customer_handle=sender,
                            provider_message_id=str(message.get("id", "")),
                        )
                    )
        return out

    def send(self, channel: Channel, recipient: str, text: str) -> None:
        if not self.configured:
            log.warning("WhatsApp not configured; reply to %s dropped", recipient[-4:])
            return
        url = f"{GRAPH}/{self.settings.whatsapp_phone_id}/messages"
        client = self._client or httpx.Client(timeout=10.0)
        try:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {self.settings.whatsapp_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": recipient,
                    "type": "text",
                    "text": {"preview_url": False, "body": text},
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("WhatsApp send failed: %s", exc.__class__.__name__)


def within_service_window(last_customer_message_at: datetime | None) -> bool:
    """Meta only allows free-form replies within 24 hours of the customer's last message."""
    if last_customer_message_at is None:
        return False
    return datetime.now(UTC).replace(tzinfo=None) - last_customer_message_at < WINDOW


def get_adapter(settings: Annotated[Settings, Depends(get_settings)]) -> WhatsAppAdapter:
    return WhatsAppAdapter(settings)


@router.get("")
def verify_webhook(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Response:
    """Meta calls this once when you save the webhook URL."""
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token")
        and params.get("hub.verify_token") == settings.whatsapp_verify_token
    ):
        return Response(params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed")


@router.post("")
async def receive(
    request: Request,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
    agent: Annotated[Agent, Depends(get_agent)],
    adapter: Annotated[WhatsAppAdapter, Depends(get_adapter)],
) -> Response:
    """Verify, deduplicate, acknowledge fast, answer in the background (Meta retries on delay)."""
    body = await request.body()
    if not adapter.verify(body, {k.lower(): v for k, v in request.headers.items()}):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad signature")

    payload = await request.json()
    for message in adapter.parse(payload):
        if not claim_message(session, Channel.whatsapp, message.provider_message_id):
            continue
        background.add_task(_handle, agent, adapter, message)
    return Response(status_code=status.HTTP_200_OK)


async def _handle(agent: Agent, adapter: WhatsAppAdapter, message: InboundMessage) -> None:
    reply = await agent.handle_message(
        message.conversation_id, message.channel, message.text, message.customer_handle
    )
    if reply.kind != "silent" and reply.text:
        adapter.send(message.channel, message.customer_handle, reply.text)
