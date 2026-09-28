"""Email over Postmark. Runs against fixtures — no Postmark account needed to prove it works."""

import base64
import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agent.llm import LLMResponse, ScriptedLLM
from app.agent.routes import get_llm
from app.channels.email import EmailAdapter, conversation_id_for, get_adapter, strip_quoted
from app.channels.outbound import build_router
from app.config import Settings, get_settings
from app.db import get_session
from app.handoff.models import Channel, Conversation
from app.knowledge_base.ingest import ingest_docs
from app.main import create_app

SETTINGS = Settings(
    postmark_server_token="pm-token",
    postmark_from="support@northwindgoods.example",
    postmark_inbound_user="hook",
    postmark_inbound_password="s3cret",
)
AUTH = {"Authorization": "Basic " + base64.b64encode(b"hook:s3cret").decode()}


def _inbound(
    text: str = "How long do returns take?",
    mid: str = "pm-1",
    sender: str = "Asha@Example.com",
    headers: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "MessageID": mid,
        "From": sender,
        "FromFull": {"Email": sender, "Name": "Asha"},
        "Subject": "Returns",
        "TextBody": text,
        "StrippedTextReply": "",
        "Headers": headers or [{"Name": "Message-ID", "Value": "<abc@mail.example.com>"}],
    }


@pytest.fixture
def sent() -> list[dict]:
    return []


@pytest.fixture
def adapter(sent: list[dict]) -> EmailAdapter:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Postmark-Server-Token"] == "pm-token"
        assert str(request.url) == "https://api.postmarkapp.com/email"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ErrorCode": 0, "MessageID": "out-1"})

    return EmailAdapter(SETTINGS, client=httpx.Client(transport=httpx.MockTransport(handle)))


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def client(session: Session, llm: ScriptedLLM, adapter: EmailAdapter) -> Iterator[TestClient]:
    ingest_docs(session, "verticals/northwind/docs")
    app = create_app()

    def _session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_adapter] = lambda: adapter
    with TestClient(app) as c:
        yield c


def test_webhook_without_the_right_credentials_is_rejected(client: TestClient, sent: list) -> None:
    body = _inbound()
    assert client.post("/channels/email", json=body).status_code == 401
    wrong = {"Authorization": "Basic " + base64.b64encode(b"hook:nope").decode()}
    assert client.post("/channels/email", json=body, headers=wrong).status_code == 401
    assert sent == []


def test_an_unconfigured_webhook_rejects_everything() -> None:
    adapter = EmailAdapter(Settings())
    assert not adapter.verify({"authorization": AUTH["Authorization"]})


def test_email_is_answered_on_the_same_thread(
    client: TestClient, llm: ScriptedLLM, sent: list, session: Session
) -> None:
    llm.queue(LLMResponse(text="You have 30 days from delivery. [S1]"))
    assert client.post("/channels/email", json=_inbound(), headers=AUTH).status_code == 200
    assert len(sent) == 1
    out = sent[0]
    assert out["To"] == "asha@example.com"
    assert out["From"] == "support@northwindgoods.example"
    assert out["Subject"] == "Re: Returns"
    assert out["TextBody"].startswith("You have 30 days from delivery.")
    headers = {h["Name"]: h["Value"] for h in out["Headers"]}
    assert headers["In-Reply-To"] == "<abc@mail.example.com>"
    assert headers["References"] == "<abc@mail.example.com>"
    conv = session.get(Conversation, conversation_id_for("asha@example.com"))
    assert conv is not None and conv.channel is Channel.email


def test_duplicate_delivery_answers_once(client: TestClient, llm: ScriptedLLM, sent: list) -> None:
    llm.queue(LLMResponse(text="30 days. [S1]"))
    for _ in range(3):  # Postmark retries on a slow or failed response
        client.post("/channels/email", json=_inbound(), headers=AUTH)
    assert len(sent) == 1


@pytest.mark.parametrize(
    "headers",
    [
        [{"Name": "Auto-Submitted", "Value": "auto-replied"}],
        [{"Name": "Precedence", "Value": "bulk"}],
    ],
)
def test_auto_replies_are_ignored(client: TestClient, sent: list, headers: list) -> None:
    """Answering an out-of-office would start a mail loop."""
    assert (
        client.post("/channels/email", json=_inbound(headers=headers), headers=AUTH).status_code
        == 200
    )
    assert sent == []


@pytest.mark.parametrize(
    "sender", ["mailer-daemon@mail.example.com", "support@northwindgoods.example"]
)
def test_bounces_and_our_own_address_are_ignored(
    client: TestClient, sent: list, sender: str
) -> None:
    client.post("/channels/email", json=_inbound(sender=sender), headers=AUTH)
    assert sent == []


def test_escalation_email_tells_the_customer(client: TestClient, sent: list) -> None:
    client.post("/channels/email", json=_inbound("Please cancel my order"), headers=AUTH)
    assert "support team" in sent[0]["TextBody"]


def test_quoted_history_is_stripped() -> None:
    text = (
        "Is it still in stock?\n\nThanks\n\n"
        "On Mon, 21 Sep 2026 at 10:00, Northwind <support@northwindgoods.example> wrote:\n"
        "> You asked about the throw.\n"
    )
    assert strip_quoted(text) == "Is it still in stock?\n\nThanks"
    assert strip_quoted("hi\n> quoted") == "hi"
    assert strip_quoted("hi\n-----Original Message-----\nold") == "hi"


def test_postmarks_stripped_reply_is_preferred() -> None:
    adapter = EmailAdapter(SETTINGS)
    payload = _inbound("Full body\n> old") | {"StrippedTextReply": "Just the new bit"}
    [message] = adapter.parse(payload)
    assert message.inbound.text == "Just the new bit"


def test_one_conversation_per_sender_whatever_the_case() -> None:
    assert conversation_id_for("Asha@Example.com ") == conversation_id_for("asha@example.com")
    assert len(conversation_id_for("x" * 190 + "@example.com")) <= 64


def test_staff_replies_go_out_by_email_once_configured(sent: list, adapter: EmailAdapter) -> None:
    router = build_router(SETTINGS)
    router.senders[Channel.email] = adapter  # the fixture's transport, same settings
    router.send(Channel.email, "asha@example.com", "Your refund is approved.")
    assert sent[0]["To"] == "asha@example.com"
    assert sent[0]["TextBody"].startswith("Your refund is approved.")


def test_router_only_offers_channels_that_can_deliver() -> None:
    assert Channel.email in build_router(SETTINGS).senders
    unconfigured = build_router(Settings())
    assert Channel.email not in unconfigured.senders
    assert Channel.whatsapp not in unconfigured.senders
    assert Channel.webchat in unconfigured.senders
