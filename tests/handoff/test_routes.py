import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_session
from app.handoff.models import Channel, EscalationReason, Mode
from app.handoff.routes import get_sender
from app.handoff.sender import RecordingSender
from app.handoff.service import conversation_mode, create_ticket
from app.main import create_app

AUTH = ("staff", "change-me")
HX = {"HX-Request": "true"}


@pytest.fixture
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def client(session: Session, sender: RecordingSender) -> Iterator[TestClient]:
    app = create_app()

    def _session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_sender] = lambda: sender
    with TestClient(app) as c:
        yield c


def _ticket(session: Session, cid: str = "c1", summary: str = "Wants a refund.") -> int:
    t = create_ticket(
        session, cid, EscalationReason.restricted_action, summary, Channel.email, "a@example.com"
    )
    return t.id


@pytest.mark.parametrize("auth", [None, ("staff", "wrong"), ("nobody", "change-me")])
def test_requires_basic_auth(client: TestClient, session: Session, auth: object) -> None:
    tid = _ticket(session)
    for method, url in [
        ("GET", "/staff"),
        ("GET", f"/staff/tickets/{tid}"),
        ("POST", f"/staff/tickets/{tid}/reply"),
        ("POST", f"/staff/tickets/{tid}/close"),
    ]:
        resp = client.request(method, url, auth=auth, headers=HX)  # type: ignore[arg-type]
        assert resp.status_code == 401, (method, url)


def test_queue_lists_open_tickets_oldest_first(client: TestClient, session: Session) -> None:
    _ticket(session, "c1", "first issue")
    _ticket(session, "c2", "second issue")
    html = client.get("/staff", auth=AUTH).text
    assert html.index("first issue") < html.index("second issue")
    assert "restricted_action" in html


def test_ticket_view_shows_summary_and_transcript(client: TestClient, session: Session) -> None:
    tid = _ticket(session)
    html = client.get(f"/staff/tickets/{tid}", auth=AUTH).text
    assert "Wants a refund." in html and "a@example.com" in html


def test_ticket_view_escapes_customer_content(client: TestClient, session: Session) -> None:
    tid = _ticket(session, summary="<script>alert(1)</script>")
    html = client.get(f"/staff/tickets/{tid}", auth=AUTH).text
    assert "<script>alert(1)</script>" not in html


def test_reply_goes_through_sender(
    client: TestClient, session: Session, sender: RecordingSender
) -> None:
    tid = _ticket(session)
    resp = client.post(
        f"/staff/tickets/{tid}/reply", data={"text": "Refund issued."}, auth=AUTH, headers=HX
    )
    assert resp.status_code == 200
    assert sender.sent == [(Channel.email, "a@example.com", "Refund issued.")]
    assert "Refund issued." in resp.text
    assert conversation_mode(session, "c1") is Mode.human


def test_post_without_htmx_header_is_rejected(
    client: TestClient, session: Session, sender: RecordingSender
) -> None:
    tid = _ticket(session)
    resp = client.post(f"/staff/tickets/{tid}/reply", data={"text": "x"}, auth=AUTH)
    assert resp.status_code == 403
    assert sender.sent == []


def test_empty_reply_is_400(client: TestClient, session: Session) -> None:
    tid = _ticket(session)
    resp = client.post(f"/staff/tickets/{tid}/reply", data={"text": " "}, auth=AUTH, headers=HX)
    assert resp.status_code == 400


def test_close_then_reply_is_409(client: TestClient, session: Session) -> None:
    tid = _ticket(session)
    resp = client.post(f"/staff/tickets/{tid}/close", auth=AUTH, headers=HX)
    assert resp.status_code == 200 and resp.headers.get("HX-Redirect") == "/staff"
    assert conversation_mode(session, "c1") is Mode.bot
    resp = client.post(f"/staff/tickets/{tid}/reply", data={"text": "x"}, auth=AUTH, headers=HX)
    assert resp.status_code == 409


def test_closed_tickets_not_in_queue(client: TestClient, session: Session) -> None:
    tid = _ticket(session, summary="gone soon")
    client.post(f"/staff/tickets/{tid}/close", auth=AUTH, headers=HX)
    assert "gone soon" not in client.get("/staff", auth=AUTH).text


def test_unknown_ticket_404(client: TestClient) -> None:
    assert client.get("/staff/tickets/999", auth=AUTH).status_code == 404


def test_queue_with_100_tickets_renders_fast(client: TestClient, session: Session) -> None:
    for i in range(100):
        _ticket(session, f"c{i}", f"issue {i}")
    client.get("/staff", auth=AUTH)  # warm templates
    t = time.perf_counter()
    resp = client.get("/staff", auth=AUTH)
    elapsed = time.perf_counter() - t
    assert resp.status_code == 200 and resp.text.count("issue ") >= 100
    assert elapsed < 0.3
