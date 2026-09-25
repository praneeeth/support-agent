"""Guardrails are additive. A vertical can tighten them and must not be able to loosen them."""

import pytest
from sqlalchemy.orm import Session

from app.agent.core import Agent
from app.agent.llm import LLMResponse, ScriptedLLM
from app.agent.policy import build_policy
from app.config import Settings
from app.handoff.models import Channel, EscalationReason
from app.knowledge_base.search import Hit
from app.verticals.config import Guardrails, Refusal, VerticalConfig

CLINIC = Guardrails(
    restricted=[r"\b(store credit|goodwill voucher)\b"],
    human=[r"\bput me on to the doctor\b"],
    refuse=[
        Refusal(
            name="medical advice",
            pattern=r"\bis this\b.{0,25}\b(normal|serious|dangerous)\b"
            r"|\bshould i be worried\b"
            r"|\bmy (rash|lump|pain|symptoms?)\b",
            reply="I'm not able to advise on symptoms. I've asked a member of the clinical team "
            "to call you back.",
        )
    ],
    reference_pattern=r"\bAPT-\d{5}\b",
)


class FakeKB:
    def __init__(self) -> None:
        self.hits = [
            Hit(
                chunk_id=1,
                doc_title="conditions.md",
                heading="Rashes",
                text="A red rash is usually contact dermatitis and clears in a week.",
                score=0.9,
                source_path="conditions.md",
            )
        ]

    def search(self, query: str, k: int = 5) -> list[Hit]:
        return self.hits[:k]


def _agent(session: Session, guardrails: Guardrails) -> Agent:
    config = VerticalConfig(
        id="clinic",
        business={"name": "Bright Smile", "kind": "a dental clinic"},  # type: ignore[arg-type]
        docs_dir="verticals/northwind/docs",
        tools=[],
        guardrails=guardrails,
    )
    return Agent(
        session=session,
        kb=FakeKB(),
        llm=ScriptedLLM(),
        settings=Settings(kb_min_score=0.35),
        vertical=config,
    )


# ---- the built-ins survive whatever a vertical says ----


@pytest.mark.parametrize(
    "guardrails",
    [Guardrails(), CLINIC, Guardrails(restricted=[r"\bnothing\b"], human=[r"\bnothing\b"])],
)
@pytest.mark.parametrize(
    "message",
    ["please cancel my order", "I want a refund", "can I speak to a human", "put me through"],
)
def test_no_config_can_switch_off_a_built_in_guard(guardrails: Guardrails, message: str) -> None:
    policy = build_policy(guardrails)
    assert policy.wants_restricted_action(message) or policy.wants_human(message)


def test_a_vertical_can_add_its_own_restricted_phrasing() -> None:
    policy = build_policy(CLINIC)
    assert policy.wants_restricted_action("can I have a goodwill voucher")
    assert not build_policy(Guardrails()).wants_restricted_action("can I have a goodwill voucher")


def test_policy_questions_are_still_answerable() -> None:
    """Adding patterns must not turn 'what is your refund policy' into a handoff."""
    assert not build_policy(CLINIC).wants_restricted_action("what is your refund policy?")


def test_the_reference_pattern_follows_the_business() -> None:
    policy = build_policy(CLINIC)
    assert policy.is_order_question("where is APT-12345")
    assert not policy.is_order_question("where is NW-482913")  # not this business's format


# ---- refuse-outright ----


async def test_it_refuses_even_when_the_documents_answer_the_question(session: Session) -> None:
    """The knowledge base *does* describe rashes. It must still not be used to advise."""
    agent = _agent(session, CLINIC)
    reply = await agent.handle_message("c1", Channel.webchat, "is this rash serious?", "w1")
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.restricted_action
    assert "not able to advise on symptoms" in reply.text
    assert "dermatitis" not in reply.text  # the document never reached the customer


async def test_the_refusal_is_logged_with_its_name_for_the_agent(session: Session) -> None:
    agent = _agent(session, CLINIC)
    await agent.handle_message("c2", Channel.webchat, "should I be worried?", "w1")
    from app.handoff.models import Ticket

    ticket = session.query(Ticket).filter(Ticket.conversation_id == "c2").one()
    assert "medical advice" in ticket.summary


async def test_without_a_refusal_the_same_question_is_answered(session: Session) -> None:
    agent = _agent(session, Guardrails())
    agent.llm.queue(LLMResponse(text="Contact dermatitis usually clears in a week. [S1]"))  # type: ignore[attr-defined]
    reply = await agent.handle_message("c3", Channel.webchat, "is this rash serious?", "w1")
    assert reply.kind == "answer"


# ---- a broken config fails at load, not at runtime ----


@pytest.mark.parametrize("bad", ["(unclosed", "*nope", "[a-"])
def test_an_invalid_pattern_is_rejected_when_the_config_loads(bad: str) -> None:
    with pytest.raises(ValueError, match="regular expression"):
        Guardrails(restricted=[bad])
    with pytest.raises(ValueError, match="regular expression"):
        Guardrails(reference_pattern=bad)
    with pytest.raises(ValueError, match="regular expression"):
        Refusal(name="x", pattern=bad, reply="no")
