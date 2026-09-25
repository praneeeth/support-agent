"""The admin portal. It must never show a credential, and it must survive an empty database."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.admin import analytics
from app.agent.llm import LLMResponse, ScriptedLLM
from app.agent.routes import get_llm
from app.config import get_settings
from app.db import get_session
from app.handoff.models import Channel, EscalationReason, Role
from app.handoff.service import create_ticket, get_or_create_conversation, record_message
from app.knowledge_base.ingest import ingest_docs
from app.main import create_app

AUTH = ("staff", "change-me")  # the default; see app/config.py


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def client(session: Session, llm: ScriptedLLM) -> Iterator[TestClient]:
    ingest_docs(session, "verticals/northwind/docs")
    app = create_app()

    def _session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_llm] = lambda: llm
    with TestClient(app) as c:
        yield c


def _conversation(session: Session, cid: str, text: str, reason: EscalationReason | None) -> None:
    get_or_create_conversation(session, cid, Channel.webchat, f"{cid}-handle")
    record_message(session, cid, Role.customer, text)
    if reason is not None:
        create_ticket(session, cid, reason, text, Channel.webchat)


ADMIN_PAGES = ["/admin", "/admin/integrations", "/admin/knowledge", "/admin/playground"]


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_every_page_needs_the_password(client: TestClient, url: str) -> None:
    assert client.get(url).status_code == 401
    assert client.get(url, auth=("staff", "wrong")).status_code == 401
    assert client.get(url, auth=AUTH).status_code == 200


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_pages_render_on_an_empty_database(client: TestClient, url: str) -> None:
    """A new client sees these before a single conversation exists."""
    body = client.get(url, auth=AUTH).text
    assert "Traceback" not in body and "Internal Server Error" not in body


def test_overview_counts_conversations_not_tickets(client: TestClient, session: Session) -> None:
    _conversation(session, "c1", "where is my order", None)
    _conversation(session, "c2", "refund please", EscalationReason.restricted_action)
    # Two tickets on one conversation must still count as one escalated conversation.
    create_ticket(session, "c2", EscalationReason.customer_requested, "again", Channel.webchat)

    data = analytics.overview(session)
    assert data.conversations == 2
    assert data.escalated == 1
    assert data.handled == 1
    assert data.handled_pct == 50

    body = client.get("/admin", auth=AUTH).text
    assert "50%" in body


def test_overview_lists_the_gaps_to_fix(client: TestClient, session: Session) -> None:
    _conversation(session, "c1", "do you do corporate gifting?", EscalationReason.low_confidence)
    body = client.get("/admin", auth=AUTH).text
    assert "corporate gifting" in body
    assert "Couldn&#39;t answer" in body or "Couldn't answer" in body


def test_daily_buckets_cover_the_whole_window(session: Session) -> None:
    today = datetime.now(UTC).date()
    data = analytics.overview(session, today=today)
    assert len(data.days) == analytics.WINDOW_DAYS
    assert data.days[-1].day == today
    assert data.days[0].day == today - timedelta(days=analytics.WINDOW_DAYS - 1)


def test_integrations_names_variables_but_shows_no_secret(client: TestClient) -> None:
    body = client.get("/admin/integrations", auth=AUTH).text
    assert "WHATSAPP_TOKEN" in body  # the variable name is fine
    assert "Not set up" in body
    assert "shopify" in body.lower()  # registered connectors are listed as available
    assert "test-pass" not in body


def test_integrations_never_prints_a_configured_secret(
    session: Session, llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "EAA-super-secret-token"  # noqa: S105 - a fake value, asserted absent below
    monkeypatch.setenv("WHATSAPP_TOKEN", secret)
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "123")
    get_settings.cache_clear()
    try:
        app = create_app()

        def _session() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_llm] = lambda: llm
        with TestClient(app) as c:
            body = c.get("/admin/integrations", auth=AUTH).text
    finally:
        get_settings.cache_clear()
    assert secret not in body
    assert "Connected" in body


def test_knowledge_lists_documents_and_search_narrows(client: TestClient) -> None:
    body = client.get("/admin/knowledge", auth=AUTH).text
    assert "returns-policy.md" in body
    narrowed = client.get("/admin/knowledge", auth=AUTH, params={"q": "returns"}).text
    assert "returns-policy.md" in narrowed
    assert "shipping-international.md" not in narrowed


def test_reindex_requires_the_htmx_header(client: TestClient) -> None:
    assert client.post("/admin/knowledge/reindex", auth=AUTH).status_code == 403
    ok = client.post("/admin/knowledge/reindex", auth=AUTH, headers={"HX-Request": "true"})
    assert ok.status_code == 200
    assert "Re-indexed" in ok.text


def test_playground_shows_scores_and_the_threshold(client: TestClient, llm: ScriptedLLM) -> None:
    llm.queue(LLMResponse(text="You have 30 days from delivery. [S1]"))
    body = client.post(
        "/admin/playground", auth=AUTH, data={"text": "how long do I have to return something?"}
    ).text
    assert "Answered" in body
    assert "What it retrieved" in body or "WHAT IT RETRIEVED" in body.upper()
    assert "Threshold" in body


def test_playground_run_stays_out_of_the_inbox(client: TestClient, llm: ScriptedLLM) -> None:
    """A test question must not look like a customer waiting for help."""
    body = client.post("/admin/playground", auth=AUTH, data={"text": "cancel my order"}).text
    assert "Handed over" in body
    inbox = client.get("/staff", auth=AUTH).text
    assert "playground-" not in inbox


def test_playground_without_a_question_just_redisplays(client: TestClient) -> None:
    resp = client.post("/admin/playground", auth=AUTH, data={"text": "   "})
    assert resp.status_code == 200
    assert "Try one of these" in resp.text
