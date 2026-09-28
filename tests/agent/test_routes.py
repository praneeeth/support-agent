import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent.llm import LLMResponse, ScriptedLLM
from app.agent.routes import get_llm
from app.db import get_session
from app.handoff.models import Channel, EscalationReason, Mode
from app.handoff.service import conversation_mode
from app.knowledge_base.ingest import ingest_docs
from app.main import create_app

BODY = {"conversation_id": "c1", "channel": "webchat", "text": "how long do returns take"}


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def client(session: Session, llm: ScriptedLLM) -> Iterator[TestClient]:
    ingest_docs(session, "data/docs")
    app = create_app()

    def _session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_llm] = lambda: llm
    with TestClient(app) as c:
        yield c


def test_answer(client: TestClient, llm: ScriptedLLM) -> None:
    llm.queue(LLMResponse(text="You have 30 days from delivery. [S1]"))
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "answer"
    assert body["text"] == "You have 30 days from delivery."
    assert body["sources"] and body["escalation_reason"] is None


def test_escalation(client: TestClient, session: Session) -> None:
    resp = client.post("/v1/messages", json={**BODY, "text": "please cancel my order"})
    body = resp.json()
    assert body["kind"] == "escalated"
    assert body["escalation_reason"] == EscalationReason.restricted_action.value
    assert conversation_mode(session, "c1") is Mode.waiting_human


def test_validation_error(client: TestClient) -> None:
    assert client.post("/v1/messages", json={**BODY, "text": ""}).status_code == 422
    assert client.post("/v1/messages", json={**BODY, "channel": "telepathy"}).status_code == 422


def test_defaults_to_webchat(client: TestClient, llm: ScriptedLLM, session: Session) -> None:
    llm.queue(LLMResponse(text="30 days. [S1]"))
    client.post("/v1/messages", json={"conversation_id": "c9", "text": "returns?"})
    from app.handoff.models import Conversation

    conv = session.get(Conversation, "c9")
    assert conv is not None and conv.channel is Channel.webchat


def test_stream_emits_deltas_then_done(client: TestClient, llm: ScriptedLLM) -> None:
    llm.queue(
        LLMResponse(
            text=(
                "You can return most items within 30 days of delivery, as long as they are "
                "unused and in the original packaging. [S1]"
            )
        )
    )
    with client.stream("POST", "/v1/messages/stream", json=BODY) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())
    deltas = [
        json.loads(line[len("data: ") :])
        for block in raw.split("\n\n")
        if block.startswith("event: delta")
        for line in block.splitlines()
        if line.startswith("data: ")
    ]
    assert len(deltas) > 1
    done = raw.split("event: done\ndata: ")[1].strip()
    payload = json.loads(done)
    assert payload["kind"] == "answer"
    assert "".join(d["text"] for d in deltas).replace(" ", "") == payload["text"].replace(" ", "")


def test_stream_for_escalation(client: TestClient) -> None:
    with client.stream(
        "POST", "/v1/messages/stream", json={**BODY, "text": "I want to speak to a human"}
    ) as resp:
        raw = "".join(resp.iter_text())
    payload = json.loads(raw.split("event: done\ndata: ")[1].strip())
    assert payload["kind"] == "escalated"
    assert payload["escalation_reason"] == EscalationReason.customer_requested.value
