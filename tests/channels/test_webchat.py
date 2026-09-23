from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent.llm import LLMResponse, ScriptedLLM
from app.agent.routes import get_llm
from app.channels import webchat
from app.db import get_session
from app.handoff.models import Conversation, Mode
from app.handoff.service import conversation_mode
from app.knowledge_base.ingest import ingest_docs
from app.main import create_app

SID = "s-abcdefgh"


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture(autouse=True)
def _clear_rate_limit() -> Iterator[None]:
    webchat._RATE.clear()
    yield
    webchat._RATE.clear()


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


def test_widget_script_is_served_with_brand(client: TestClient) -> None:
    resp = client.get("/chat/widget.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert "Northwind Goods" in resp.text
    assert "__BRAND__" not in resp.text and "__GREETING__" not in resp.text
    assert "/chat/message" in resp.text


def test_demo_page_embeds_the_widget(client: TestClient) -> None:
    html = client.get("/chat/demo").text
    assert '<script src="/chat/widget.js"' in html


def test_answer_round_trip(client: TestClient, llm: ScriptedLLM) -> None:
    llm.queue(LLMResponse(text="You have 30 days from delivery. [S1]"))
    resp = client.post("/chat/message", json={"session_id": SID, "text": "returns window?"})
    body = resp.json()
    assert body["kind"] == "answer"
    assert body["text"] == "You have 30 days from delivery."
    assert body["sources"] and body["handed_off"] is False


def test_conversation_id_is_stable_per_session(
    client: TestClient, llm: ScriptedLLM, session: Session
) -> None:
    llm.queue(LLMResponse(text="30 days. [S1]"), LLMResponse(text="Yes, free. [S1]"))
    client.post("/chat/message", json={"session_id": SID, "text": "returns?"})
    client.post("/chat/message", json={"session_id": SID, "text": "is it free?"})
    conv = session.get(Conversation, f"web-{SID}")
    assert conv is not None
    assert len([m for m in conv.messages if m.role.value == "customer"]) == 2


def test_escalation_is_flagged_for_the_widget(client: TestClient, session: Session) -> None:
    resp = client.post("/chat/message", json={"session_id": SID, "text": "cancel my order please"})
    body = resp.json()
    assert body["kind"] == "escalated" and body["handed_off"] is True
    assert conversation_mode(session, f"web-{SID}") is Mode.waiting_human


def test_empty_and_oversized_messages_rejected(client: TestClient) -> None:
    assert client.post("/chat/message", json={"session_id": SID, "text": ""}).status_code == 422
    big = {"session_id": SID, "text": "x" * 2001}
    assert client.post("/chat/message", json=big).status_code == 422
    assert (
        client.post("/chat/message", json={"session_id": "short", "text": "hi"}).status_code == 422
    )


def test_rate_limit_per_session(client: TestClient, llm: ScriptedLLM) -> None:
    for _ in range(webchat.RATE_LIMIT):
        llm.queue(LLMResponse(text="30 days. [S1]"))
    for _ in range(webchat.RATE_LIMIT):
        assert (
            client.post("/chat/message", json={"session_id": SID, "text": "q"}).status_code == 200
        )
    blocked = client.post("/chat/message", json={"session_id": SID, "text": "q"})
    assert blocked.status_code == 429
    # A different visitor is unaffected.
    llm.queue(LLMResponse(text="30 days. [S1]"))
    assert (
        client.post("/chat/message", json={"session_id": "s-other123", "text": "q"}).status_code
        == 200
    )
