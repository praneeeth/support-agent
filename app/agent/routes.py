"""HTTP entry points used by the channel modules (and for manual testing)."""

import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.core import Agent, AgentReply
from app.agent.llm import AnthropicLLM, LLMClient
from app.config import Settings, get_settings
from app.db import get_session
from app.handoff.models import Channel
from app.knowledge_base.embed import Embedder, FastEmbedder
from app.knowledge_base.search import KnowledgeBase

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")

_embedder: Embedder | None = None
_embedder_loaded = False


def _get_embedder(settings: Settings) -> Embedder | None:
    global _embedder, _embedder_loaded
    if not _embedder_loaded:
        _embedder_loaded = True
        try:
            _embedder = FastEmbedder(settings.embedding_model)
        except Exception as exc:  # noqa: BLE001 - keyword search still works without it
            log.warning("Embedding model unavailable (%s); using keyword search.", exc)
            _embedder = None
    return _embedder


def get_llm() -> LLMClient:
    settings = get_settings()
    return AnthropicLLM(api_key=settings.anthropic_api_key, model=settings.anthropic_model)


def get_agent(
    session: Annotated[Session, Depends(get_session)],
    llm: Annotated[LLMClient, Depends(get_llm)],
) -> Agent:
    settings = get_settings()
    kb = KnowledgeBase.load(session, _get_embedder(settings))
    return Agent(session=session, kb=kb, llm=llm, settings=settings)


class MessageIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    channel: Channel = Channel.webchat
    text: str = Field(min_length=1, max_length=4000)
    customer_handle: str = Field(default="", max_length=200)


class MessageOut(BaseModel):
    kind: str
    text: str
    sources: list[str] = []
    escalation_reason: str | None = None

    @classmethod
    def of(cls, reply: AgentReply) -> "MessageOut":
        return cls(
            kind=reply.kind,
            text=reply.text,
            sources=list(reply.sources),
            escalation_reason=reply.escalation_reason.value if reply.escalation_reason else None,
        )


@router.post("/messages", response_model=MessageOut)
async def post_message(body: MessageIn, agent: Annotated[Agent, Depends(get_agent)]) -> MessageOut:
    reply = await agent.handle_message(
        body.conversation_id, body.channel, body.text, body.customer_handle
    )
    return MessageOut.of(reply)


@router.post("/messages/stream")
async def post_message_stream(
    body: MessageIn, agent: Annotated[Agent, Depends(get_agent)]
) -> StreamingResponse:
    """Server-sent events. The model call itself is not streamed yet: the finished reply is
    chunked so channels can render it progressively."""
    reply = await agent.handle_message(
        body.conversation_id, body.channel, body.text, body.customer_handle
    )

    async def events() -> AsyncIterator[str]:
        for chunk in _chunks(reply.text):
            yield f"event: delta\ndata: {json.dumps({'text': chunk})}\n\n"
        yield f"event: done\ndata: {MessageOut.of(reply).model_dump_json()}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _chunks(text: str, size: int = 60) -> Iterator[str]:
    words, buffer = text.split(), ""
    for word in words:
        buffer = f"{buffer} {word}".strip()
        if len(buffer) >= size:
            yield buffer
            buffer = ""
    if buffer:
        yield buffer
