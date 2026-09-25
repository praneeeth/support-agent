"""The tool registry. A vertical chooses tools; it can never remove the way out."""

import pytest
from sqlalchemy.orm import Session

from app.agent.llm import ToolCall
from app.agent.tools import (
    ESCALATE,
    ToolContext,
    UnknownTool,
    available,
    resolve,
    run_tool,
    schemas,
)
from app.handoff.models import EscalationReason
from app.verticals.config import Business, load_vertical

BUSINESS = load_vertical("northwind").business
EUROS = Business(name="Ada", kind="a bike shop", currency_symbol="€")


def _context(session: Session, business: Business = BUSINESS) -> ToolContext:
    return ToolContext(session=session, conversation_id="c1", business=business)


def test_escalate_is_always_present_even_when_nothing_is_configured() -> None:
    tools = resolve([])
    assert [t.name for t in tools] == [ESCALATE]


def test_escalate_cannot_be_duplicated_by_listing_it() -> None:
    names = [t.name for t in resolve([ESCALATE, "get_product", ESCALATE])]
    assert names.count(ESCALATE) == 1
    assert names[-1] == ESCALATE  # and it comes last


def test_northwind_gets_the_tools_it_asks_for() -> None:
    names = [t.name for t in resolve(load_vertical("northwind").tools)]
    assert names == ["get_order_status", "get_product", ESCALATE]


def test_an_unknown_tool_fails_loudly_and_says_what_exists() -> None:
    with pytest.raises(UnknownTool) as caught:
        resolve(["check_availability"])
    message = str(caught.value)
    assert "check_availability" in message
    assert "get_order_status" in message  # tells you what you could have meant


def test_available_hides_escalate_because_it_is_not_a_choice() -> None:
    assert ESCALATE not in available()
    assert "get_product" in available()


def test_schemas_are_what_the_model_is_offered() -> None:
    tools = resolve(["get_product"])
    offered = schemas(tools)
    assert [s["name"] for s in offered] == ["get_product", ESCALATE]
    assert all("input_schema" in s for s in offered)


def test_a_tool_this_vertical_did_not_enable_is_refused(session: Session) -> None:
    """Defence in depth: the model was never offered it, but if it invents the call we hand over."""
    tools = resolve([])  # escalate only
    result = run_tool(
        _context(session),
        ToolCall("t1", "get_order_status", {"order_number": "NW-1", "email": "a@b.c"}),
        tools,
    )
    assert result.escalate is EscalationReason.low_confidence
    assert "unknown tool" in (result.summary or "").lower()


def test_invalid_arguments_hand_over_rather_than_guess(session: Session) -> None:
    tools = resolve(["get_order_status"])
    result = run_tool(_context(session), ToolCall("t1", "get_order_status", {}), tools)
    assert result.escalate is EscalationReason.low_confidence


def test_product_prices_use_the_vertical_currency(session: Session) -> None:
    from app.orders.seed import seed_store

    seed_store(session, seed=42)
    tools = resolve(["get_product"])
    rupees = run_tool(
        _context(session), ToolCall("t1", "get_product", {"query": "Wool Throw"}), tools
    )
    euros = run_tool(
        _context(session, EUROS), ToolCall("t2", "get_product", {"query": "Wool Throw"}), tools
    )
    assert "₹" in rupees.content
    assert "€" in euros.content and "₹" not in euros.content


def test_escalate_carries_the_reason_the_model_chose(session: Session) -> None:
    tools = resolve([])
    result = run_tool(
        _context(session),
        ToolCall("t1", ESCALATE, {"reason": "restricted_action", "summary": "Wants a refund."}),
        tools,
    )
    assert result.escalate is EscalationReason.restricted_action
    assert result.summary == "Wants a refund."
