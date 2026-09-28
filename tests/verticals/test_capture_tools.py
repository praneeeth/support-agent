"""Task 26: capture tools. They take details and make a ticket; they never schedule or promise."""

import pytest
from sqlalchemy.orm import Session

from app.agent.llm import ToolCall
from app.agent.tools import ToolContext, contact_ok, resolve, run_tool
from app.handoff.models import EscalationReason
from app.verticals.config import Business

BUSINESS = Business(name="Bright Smile", kind="a dental clinic")
TOOLS = resolve(["appointment_request", "lead_capture"])


def _run(session: Session, name: str, args: dict[str, object]):  # type: ignore[no-untyped-def]
    return run_tool(ToolContext(session, "c1", BUSINESS), ToolCall("t1", name, args), TOOLS)


def test_appointment_request_becomes_a_ticket(session: Session) -> None:
    result = _run(
        session,
        "appointment_request",
        {
            "name": "Ravi Kumar",
            "contact": "+91 98450 11111",
            "preferred_time": "Tuesday morning",
            "visit_type": "cleaning",
        },
    )
    assert result.escalate is EscalationReason.appointment_request
    summary = result.summary or ""
    for part in ("Ravi Kumar", "+91 98450 11111", "Tuesday morning", "cleaning"):
        assert part in summary


def test_lead_becomes_a_ticket(session: Session) -> None:
    result = _run(
        session,
        "lead_capture",
        {
            "name": "Sara",
            "contact": "sara@example.com",
            "interest": "2BHK to rent in Baner",
            "budget": "₹35,000 a month",
            "timeline": "by November",
        },
    )
    assert result.escalate is EscalationReason.lead
    summary = result.summary or ""
    for part in ("Sara", "sara@example.com", "2BHK to rent in Baner", "₹35,000", "November"):
        assert part in summary


@pytest.mark.parametrize("tool", ["appointment_request", "lead_capture"])
def test_no_way_to_reply_means_nothing_is_passed_on(session: Session, tool: str) -> None:
    args: dict[str, object] = {"name": "A", "contact": "later"}
    args |= {"preferred_time": "any"} if tool == "appointment_request" else {"interest": "x"}
    result = _run(session, tool, args)
    assert result.escalate is None
    assert "nothing has been passed on" in result.content.lower()


@pytest.mark.parametrize("tool", ["appointment_request", "lead_capture"])
def test_missing_details_hand_over_rather_than_guess(session: Session, tool: str) -> None:
    result = _run(session, tool, {"name": "A"})
    assert result.escalate is EscalationReason.low_confidence


@pytest.mark.parametrize(
    ("contact", "ok"),
    [
        ("a@example.com", True),
        ("+91 98450 12345", True),
        ("98450-12345", True),
        ("call me", False),
        ("12345", False),
        ("a@b", False),
    ],
)
def test_what_counts_as_a_contact(contact: str, ok: bool) -> None:
    assert contact_ok(contact) is ok
