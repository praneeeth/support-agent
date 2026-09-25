"""Task 25: a homestay never haggles, never confirms, never promises dates it didn't check."""

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.agent.core import Agent
from app.agent.llm import LLMResponse, ScriptedLLM, ToolCall
from app.agent.policy import build_policy
from app.agent.prompts import system_prompt
from app.config import Settings
from app.handoff.models import Channel, EscalationReason
from app.knowledge_base.search import Hit
from app.verticals.config import Claim, Guardrails, VerticalConfig, load_vertical

HOMESTAY = load_vertical("seaside-homestay")
POLICY = build_policy(HOMESTAY.guardrails)


@pytest.mark.parametrize(
    "text",
    [
        "Can you give me a discount on the Sea View Room?",
        "Airbnb shows it cheaper. Match that price and I'll book.",
        "Could you do a better rate for 3 nights?",
        "Please confirm my booking for next week.",
        "Can you hold the cottage for me until tomorrow?",
        "Reserve the Garden Room for us",
    ],
)
def test_owner_only_requests_are_caught_before_the_model(text: str) -> None:
    assert POLICY.wants_restricted_action(text)


@pytest.mark.parametrize(
    "text",
    [
        "Do you have a discount for long stays?",
        "How does booking work?",
        "What is your cancellation policy?",
        "Is the Garden Room good for a family?",
    ],
)
def test_questions_about_prices_and_booking_are_still_answered(text: str) -> None:
    assert not POLICY.wants_restricted_action(text)


def test_asking_for_the_owner_is_a_request_for_a_person() -> None:
    assert POLICY.wants_human("Can I talk to the owner directly?")


def test_date_questions_reach_the_availability_tool() -> None:
    """They skip the retrieval gate, as order-status questions do in the shop."""
    assert POLICY.is_order_question("Anything free from 2027-02-10 to 2027-02-12?")
    assert POLICY.is_order_question("What's your availability in March?")


def test_the_prompt_gains_the_homestay_rules_and_keeps_the_built_in_ones() -> None:
    prompt = system_prompt(HOMESTAY.business, HOMESTAY.guardrails.never_say)
    assert "Never say or imply that a room, date or booking is confirmed" in prompt
    assert "Never offer, negotiate or promise a discount" in prompt
    assert "Answer ONLY from the numbered sources" in prompt  # built-ins survive


def test_northwind_prompt_is_unchanged() -> None:
    northwind = load_vertical("northwind")
    assert system_prompt(northwind.business, northwind.guardrails.never_say) == system_prompt(
        northwind.business
    )


@pytest.mark.parametrize(
    ("answer", "grounded", "blocked"),
    [
        ("Good news, the Garden Room is available from 10 to 12 Feb. [S1]", set(), True),
        (
            "Good news, the Garden Room is available from 10 to 12 Feb.",
            {"check_availability"},
            False,
        ),
        ("Your booking is confirmed for 10 Feb. [S1]", {"check_availability"}, True),
        ("I've reserved the cottage for you. [S1]", set(), True),
        ("Your booking is confirmed only when the 30% advance is paid. [S1]", set(), False),
        ("Check-in is from 1pm. [S1]", set(), False),
    ],
)
def test_answers_that_promise_what_no_tool_returned_are_held_back(
    answer: str, grounded: set[str], blocked: bool
) -> None:
    assert (POLICY.unbacked_claim(answer, grounded) is not None) is blocked


def test_a_claim_must_name_a_tool_the_vertical_has() -> None:
    with pytest.raises(ValueError, match="teleport"):
        VerticalConfig(
            id="x",
            business=HOMESTAY.business,
            docs_dir="docs",
            tools=["check_availability"],
            guardrails=Guardrails(
                claims=[Claim(name="x", pattern="free", requires_tool="teleport")]
            ),
        )


# ---------------------------------------------------------------- through the agent


class FakeKB:
    def search(self, query: str, k: int = 5) -> list[Hit]:
        text = "Garden Room: sleeps 3, ₹3,800 a night."
        return [Hit(1, "Rooms", "Rooms", text, 0.9, "rooms-overview.md")]


def _agent(session: Session, llm: ScriptedLLM, vertical: VerticalConfig = HOMESTAY) -> Agent:
    return Agent(session=session, kb=FakeKB(), llm=llm, settings=Settings(), vertical=vertical)


async def test_an_unchecked_availability_promise_hands_over(session: Session) -> None:
    llm = ScriptedLLM().queue(
        LLMResponse(text="Yes, the Garden Room is available from 10 to 12 Feb. [S1]")
    )
    reply = await _agent(session, llm).handle_message(
        "c1", Channel.webchat, "Is the Garden Room free 2027-02-10 to 2027-02-12?"
    )
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.low_confidence
    assert "available" not in reply.text


async def test_a_checked_availability_answer_goes_out(session: Session) -> None:
    check_in = date.today() + timedelta(days=40)
    args = {"check_in": check_in.isoformat(), "check_out": (check_in + timedelta(2)).isoformat()}
    llm = ScriptedLLM().queue(
        LLMResponse(tool_calls=(ToolCall("t1", "check_availability", args),)),
        LLMResponse(text="The Garden Room is available for those dates at ₹3,800 a night."),
    )
    reply = await _agent(session, llm).handle_message(
        "c2", Channel.webchat, f"Is the Garden Room free {args['check_in']}?"
    )
    assert reply.kind == "answer"
    assert "available" in reply.text


async def test_the_model_is_told_the_homestay_rules(session: Session) -> None:
    llm = ScriptedLLM().queue(LLMResponse(text="The Garden Room sleeps 3. [S1]"))
    await _agent(session, llm).handle_message("c3", Channel.webchat, "How many fit in Garden?")
    assert "Never offer, negotiate or promise a discount" in llm.calls[0]["system"]
