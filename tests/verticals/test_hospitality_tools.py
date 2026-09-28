"""Hospitality tools: say what's free, pass on an enquiry. Never book, never confirm."""

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.agent import availability
from app.agent.llm import ToolCall
from app.agent.tools import ESCALATE, ToolContext, resolve, run_tool, schemas
from app.handoff.models import EscalationReason
from app.integrations.base import ConnectorError
from app.verticals.config import Availability, VerticalConfig

TODAY = date.today()


def _d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


STAY = VerticalConfig(
    id="stay",
    business={"name": "Seaside", "kind": "a guest house", "currency_symbol": "₹"},  # type: ignore[arg-type]
    docs_dir="verticals/northwind/docs",
    tools=["check_availability", "booking_enquiry"],
    availability=Availability(
        min_nights=2,
        max_nights=14,
        horizon_days=365,
        rooms=[
            {"name": "Sea View Room", "sleeps": 2, "nightly_rate": 4500},  # type: ignore[list-item]
            {"name": "Garden Cottage", "sleeps": 4, "nightly_rate": 6500},  # type: ignore[list-item]
        ],
        blocked=[
            {"room": "Sea View Room", "start": _d(30), "end": _d(35)},  # type: ignore[list-item]
        ],
    ),
)
TOOLS = resolve(STAY.tools)


def _run(session: Session, name: str, args: dict[str, object], vertical: VerticalConfig = STAY):
    context = ToolContext(session, "c1", vertical.business, vertical=vertical)
    return run_tool(context, ToolCall("t1", name, args), resolve(vertical.tools))


@pytest.fixture(autouse=True)
def _no_ical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(availability, "_ical_urls", lambda: "")


def test_both_tools_are_offered_and_escalate_is_still_last() -> None:
    assert [s["name"] for s in schemas(TOOLS)] == [
        "check_availability",
        "booking_enquiry",
        ESCALATE,
    ]


def test_free_dates_list_rooms_and_totals(session: Session) -> None:
    result = _run(session, "check_availability", {"check_in": _d(10), "check_out": _d(12)})
    assert result.grounded and result.escalate is None
    assert "Sea View Room" in result.content and "Garden Cottage" in result.content
    assert "₹9,000" in result.content  # 2 nights x 4,500
    assert "not a booking" in result.content.lower()


def test_blocked_room_is_reported_unavailable(session: Session) -> None:
    result = _run(session, "check_availability", {"check_in": _d(31), "check_out": _d(33)})
    assert result.grounded
    assert "Garden Cottage" in result.content
    assert "Sea View Room: not available" in result.content


def test_rooms_too_small_for_the_party_are_left_out(session: Session) -> None:
    result = _run(
        session, "check_availability", {"check_in": _d(10), "check_out": _d(12), "guests": 3}
    )
    assert "Garden Cottage" in result.content
    assert "Sea View Room" not in result.content


@pytest.mark.parametrize(
    ("check_in", "check_out", "why"),
    [
        (-3, 1, "past"),
        (10, 10, "after"),
        (10, 11, "minimum stay"),
        (10, 30, "maximum stay"),
        (400, 403, "too far ahead"),
    ],
)
def test_impossible_requests_are_explained_not_guessed(
    session: Session, check_in: int, check_out: int, why: str
) -> None:
    result = _run(
        session, "check_availability", {"check_in": _d(check_in), "check_out": _d(check_out)}
    )
    assert not result.grounded and result.escalate is None
    assert why in result.content.lower()


def test_unparseable_dates_hand_over(session: Session) -> None:
    result = _run(session, "check_availability", {"check_in": "next friday", "check_out": "x"})
    assert result.escalate is EscalationReason.low_confidence


def test_a_calendar_that_is_down_hands_over(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        def busy(self) -> list[tuple[date, date]]:
            raise ConnectorError("TimeoutError fetching a calendar")

    monkeypatch.setattr(availability, "_calendar", lambda: Broken())
    result = _run(session, "check_availability", {"check_in": _d(10), "check_out": _d(12)})
    assert result.escalate is EscalationReason.low_confidence
    assert "couldn't check" in (result.summary or "").lower()


def test_ical_busy_dates_block_every_room(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Calendar:
        def busy(self) -> list[tuple[date, date]]:
            return [(TODAY + timedelta(days=9), TODAY + timedelta(days=13))]

    monkeypatch.setattr(availability, "_calendar", lambda: Calendar())
    result = _run(session, "check_availability", {"check_in": _d(10), "check_out": _d(12)})
    assert result.grounded
    assert "Sea View Room: not available" in result.content
    assert "Garden Cottage: not available" in result.content


def test_a_vertical_without_rooms_cannot_answer(session: Session) -> None:
    bare = STAY.model_copy(update={"availability": None})
    result = _run(
        session, "check_availability", {"check_in": _d(10), "check_out": _d(12)}, vertical=bare
    )
    assert result.escalate is EscalationReason.low_confidence


def test_enquiry_becomes_a_ticket_with_everything_the_owner_needs(session: Session) -> None:
    result = _run(
        session,
        "booking_enquiry",
        {
            "check_in": _d(10),
            "check_out": _d(12),
            "guests": 2,
            "name": "Asha Rao",
            "contact": "asha@example.com",
            "room": "Sea View Room",
            "notes": "Late arrival",
        },
    )
    assert result.escalate is EscalationReason.booking_enquiry
    summary = result.summary or ""
    for part in ("Asha Rao", "asha@example.com", "2 guests", _d(10), _d(12), "Sea View Room"):
        assert part in summary
    assert "Late arrival" in summary


def test_enquiry_without_a_way_to_reply_is_refused(session: Session) -> None:
    result = _run(
        session,
        "booking_enquiry",
        {"check_in": _d(10), "check_out": _d(12), "guests": 2, "name": "A", "contact": "soon"},
    )
    assert result.escalate is None
    assert "email or phone" in result.content.lower()


def test_the_model_cannot_file_an_enquiry_through_escalate(session: Session) -> None:
    """Enquiry reasons are only reachable through the tools that capture the details."""
    offered = next(s for s in schemas(TOOLS) if s["name"] == ESCALATE)
    reasons = offered["input_schema"]["properties"]["reason"]["enum"]
    assert "booking_enquiry" not in reasons
    result = _run(session, ESCALATE, {"reason": "booking_enquiry", "summary": "x"})
    assert result.escalate is EscalationReason.low_confidence  # invalid input → handover
