"""Smoke test against the real Anthropic API. Skipped unless a key and model are configured."""

import os

import pytest
from sqlalchemy.orm import Session

from app.agent.core import Agent
from app.agent.routes import build_llm
from app.config import get_settings
from app.handoff.models import Channel, EscalationReason
from app.knowledge_base.embed import HashingEmbedder
from app.knowledge_base.ingest import ingest_docs
from app.knowledge_base.search import KnowledgeBase

pytestmark = pytest.mark.live


@pytest.fixture
def agent(session: Session) -> Agent:
    settings = get_settings()
    if os.getenv("LIVE_LLM") != "1":
        pytest.skip("set LIVE_LLM=1 to run against the configured provider")
    ingest_docs(session, settings.docs_dir, embedder=HashingEmbedder())
    kb = KnowledgeBase.load(session, HashingEmbedder())
    llm = build_llm(settings)  # Ollama, Groq, Anthropic — whatever .env points at
    return Agent(session=session, kb=kb, llm=llm, settings=settings)


async def test_real_model_answers_a_policy_question(agent: Agent) -> None:
    reply = await agent.handle_message(
        "live-1", Channel.webchat, "how many days do I have to return something?"
    )
    assert reply.kind == "answer"
    assert "30" in reply.text
    assert reply.sources


async def test_real_model_escalates_a_refund_request(agent: Agent) -> None:
    reply = await agent.handle_message(
        "live-2", Channel.webchat, "I want my money back for this order"
    )
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.restricted_action
