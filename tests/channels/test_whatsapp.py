"""WhatsApp adapter. Runs entirely against fixtures — no Meta account needed to prove it works."""

import hashlib
import hmac
import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent.llm import LLMResponse, ScriptedLLM
from app.agent.routes import get_llm
from app.channels.whatsapp import WhatsAppAdapter, get_adapter, within_service_window
from app.config import Settings, get_settings
from app.db import get_session
from app.handoff.models import Channel, Conversation
from app.knowledge_base.ingest import ingest_docs
from app.main import create_app

SECRET = "app-secret"
SETTINGS = Settings(
    whatsapp_token="wa-token",
    whatsapp_phone_id="123456",
    whatsapp_app_secret=SECRET,
    whatsapp_verify_token="verify-me",
)


def _payload(text: str = "how long do returns take", mid: str = "wamid.1") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": mid,
                                    "from": "919800000001",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }


def _signed(body: bytes) -> dict[str, str]:
    mac = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={mac}", "Content-Type": "application/json"}


@pytest.fixture
def sent() -> list[tuple[str, str]]:
    return []


@pytest.fixture
def adapter(sent: list[tuple[str, str]]) -> WhatsAppAdapter:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer wa-token"
        assert "/123456/messages" in str(request.url)
        sent.append((body["to"], body["text"]["body"]))
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    return WhatsAppAdapter(SETTINGS, client=httpx.Client(transport=httpx.MockTransport(handle)))


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def client(session: Session, llm: ScriptedLLM, adapter: WhatsAppAdapter) -> Iterator[TestClient]:
    ingest_docs(session, "data/docs")
    app = create_app()

    def _session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_adapter] = lambda: adapter
    with TestClient(app) as c:
        yield c


def test_webhook_verification_handshake(client: TestClient) -> None:
    ok = client.get(
        "/channels/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"},
    )
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get(
        "/channels/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"},
    )
    assert bad.status_code == 403


def test_unsigned_webhook_is_rejected(client: TestClient, sent: list) -> None:
    body = json.dumps(_payload()).encode()
    assert client.post("/channels/whatsapp", content=body).status_code == 401
    assert (
        client.post(
            "/channels/whatsapp", content=body, headers={"X-Hub-Signature-256": "sha256=deadbeef"}
        ).status_code
        == 401
    )
    assert sent == []


def test_message_is_answered_and_sent_back(
    client: TestClient, llm: ScriptedLLM, sent: list, session: Session
) -> None:
    llm.queue(LLMResponse(text="You have 30 days from delivery. [S1]"))
    body = json.dumps(_payload()).encode()
    assert client.post("/channels/whatsapp", content=body, headers=_signed(body)).status_code == 200
    assert sent == [("919800000001", "You have 30 days from delivery.")]
    assert session.get(Conversation, "wa-919800000001") is not None


def test_duplicate_delivery_answers_once(client: TestClient, llm: ScriptedLLM, sent: list) -> None:
    llm.queue(LLMResponse(text="30 days. [S1]"))
    body = json.dumps(_payload()).encode()
    for _ in range(3):  # Meta retries the same message id
        client.post("/channels/whatsapp", content=body, headers=_signed(body))
    assert len(sent) == 1


def test_escalation_is_sent_to_the_customer(client: TestClient, sent: list) -> None:
    body = json.dumps(_payload(text="cancel my order please", mid="wamid.2")).encode()
    client.post("/channels/whatsapp", content=body, headers=_signed(body))
    assert len(sent) == 1 and "support team" in sent[0][1]


def test_non_text_messages_are_ignored(adapter: WhatsAppAdapter) -> None:
    payload = _payload()
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "image"
    assert adapter.parse(payload) == []


def test_parse_handles_status_only_payloads(adapter: WhatsAppAdapter) -> None:
    assert adapter.parse({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) == []


def test_unconfigured_adapter_sends_nothing(sent: list) -> None:
    quiet = WhatsAppAdapter(Settings())
    assert quiet.configured is False
    quiet.send(Channel.whatsapp, "919800000001", "hello")  # must not raise
    assert sent == []


def test_service_window() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    assert within_service_window(now - timedelta(hours=2)) is True
    assert within_service_window(now - timedelta(hours=30)) is False
    assert within_service_window(None) is False
