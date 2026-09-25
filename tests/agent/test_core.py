"""One test per numbered behaviour in spec/SPEC-agent-core.md, using the scripted LLM."""

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.core import MAX_TOOL_ROUNDS, Agent, AgentReply
from app.agent.llm import LLMError, LLMResponse, ScriptedLLM, ToolCall
from app.agent.prompts import clarify_text
from app.config import Settings
from app.handoff.models import Channel, EscalationReason, Mode, Role, Ticket
from app.handoff.service import conversation_mode, create_ticket
from app.knowledge_base.search import Hit
from app.verticals.config import load_vertical

CID = "conv-1"
CLARIFY_TEXT = clarify_text(load_vertical("northwind").business)


class FakeKB:
    """Returns fixed hits; `score` drives the retrieval gate."""

    def __init__(self, hits: list[Hit] | None = None) -> None:
        self.hits = hits if hits is not None else [_hit("returns-policy.md", 0.8)]
        self.queries: list[str] = []

    def search(self, query: str, k: int = 5) -> list[Hit]:
        self.queries.append(query)
        return self.hits[:k]


def _hit(path: str, score: float, text: str = "Returns take 30 days.") -> Hit:
    return Hit(chunk_id=1, doc_title=path, heading="H", text=text, score=score, source_path=path)


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def kb() -> FakeKB:
    return FakeKB()


@pytest.fixture
def agent(session: Session, kb: FakeKB, llm: ScriptedLLM) -> Agent:
    return Agent(session=session, kb=kb, llm=llm, settings=Settings(kb_min_score=0.35))


@pytest.fixture
def seeded_orders(session: Session) -> Iterator[Session]:
    from app.orders.seed import seed_store

    seed_store(session, seed=42)
    yield session


async def _send(agent: Agent, text: str, cid: str = CID) -> AgentReply:
    return await agent.handle_message(cid, Channel.webchat, text, "widget-1")


# 1 — a human owns the conversation
async def test_silent_while_human_owns_conversation(
    agent: Agent, session: Session, llm: ScriptedLLM
) -> None:
    llm.queue(LLMResponse(text="Hi! How can I help? [S1]"))
    await _send(agent, "hello")  # creates the conversation
    llm.responses.clear()
    llm.calls.clear()
    create_ticket(session, CID, EscalationReason.customer_requested, "s", Channel.webchat)
    reply = await _send(agent, "any update?")
    assert reply.kind == "silent" and reply.text == ""
    assert llm.calls == [] or all(c["max_tokens"] == 5 for c in llm.calls)
    from app.handoff.models import Conversation

    conv = session.get(Conversation, CID)
    assert conv is not None
    assert "any update?" in [m.text for m in conv.messages]


# 2a — explicit request for a human
@pytest.mark.parametrize(
    "text",
    [
        "can I speak to a human please",
        "connect me to an agent",
        "I want to talk to a real person",
        "escalate this",
    ],
)
async def test_human_request_escalates(agent: Agent, session: Session, text: str) -> None:
    reply = await _send(agent, text)
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.customer_requested
    assert conversation_mode(session, CID) is Mode.waiting_human
    ticket = session.scalar(select(Ticket))
    assert ticket is not None and text[:20] in ticket.summary


# 2b — restricted actions never reach the model
@pytest.mark.parametrize(
    "text",
    [
        "please cancel my order NW-482913",
        "I want a refund for this",
        "can you return my parcel",
        "I'd like to exchange my towels",
        "change my payment method",
    ],
)
async def test_restricted_action_escalates_without_calling_the_model(
    agent: Agent, llm: ScriptedLLM, text: str
) -> None:
    reply = await _send(agent, text)
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.restricted_action
    assert llm.calls == []


@pytest.mark.parametrize(
    "text",
    [
        "what is your refund policy",
        "how long do I have to return something",
        "do you offer exchanges",
    ],
)
async def test_policy_questions_are_answered_not_escalated(
    agent: Agent, llm: ScriptedLLM, text: str
) -> None:
    llm.queue(LLMResponse(text="You have 30 days from delivery. [S1]"))
    reply = await _send(agent, text)
    assert reply.kind == "answer"


# 3 — retrieval gate
async def test_below_threshold_clarifies_without_calling_the_model(
    agent: Agent, kb: FakeKB, llm: ScriptedLLM
) -> None:
    kb.hits = [_hit("random.md", 0.10)]
    reply = await _send(agent, "what's the weather in Paris")
    assert reply.kind == "clarify" and reply.text == CLARIFY_TEXT
    assert llm.calls == []


async def test_order_question_bypasses_the_threshold(
    agent: Agent, kb: FakeKB, llm: ScriptedLLM, seeded_orders: Session
) -> None:
    kb.hits = [_hit("random.md", 0.05)]
    llm.queue(LLMResponse(tool_calls=(ToolCall("t1", "get_order_status", {}),)))
    await _send(agent, "where is my order NW-482913")
    assert llm.calls, "the model should still be called for order questions"


# 4 — the model gets the sources and the history
async def test_model_receives_sources_and_previous_turns(
    agent: Agent, session: Session, llm: ScriptedLLM
) -> None:
    llm.queue(LLMResponse(text="Yes. [S1]"), LLMResponse(text="Still yes. [S1]"))
    await _send(agent, "do you ship to Delhi", cid="c2")
    await _send(agent, "and to Pune", cid="c2")
    last = llm.calls[-1]
    assert last["messages"][0] == {"role": "user", "content": "do you ship to Delhi"}
    assert last["messages"][1]["role"] == "assistant"
    assert "<sources>" in last["messages"][-1]["content"]
    assert "[S1] Returns take 30 days." in last["messages"][-1]["content"]
    assert last["tools"] == ["get_order_status", "get_product", "escalate"]


# 5/6 — citations
async def test_cited_answer_is_returned_with_sources(agent: Agent, llm: ScriptedLLM) -> None:
    llm.queue(LLMResponse(text="You can return items within 30 days. [S1]"))
    reply = await _send(agent, "how long do returns take")
    assert reply.kind == "answer"
    assert reply.text == "You can return items within 30 days."  # marker stripped
    assert reply.sources == ("returns-policy.md",)


async def test_uncited_answer_becomes_a_miss(agent: Agent, llm: ScriptedLLM) -> None:
    llm.queue(LLMResponse(text="I think it's about a month."))
    reply = await _send(agent, "how long do returns take")
    assert reply.kind == "clarify"


async def test_citation_to_a_source_that_was_not_given_is_a_miss(
    agent: Agent, llm: ScriptedLLM
) -> None:
    llm.queue(LLMResponse(text="Thirty days. [S4]"))
    assert (await _send(agent, "how long do returns take")).kind == "clarify"


# 7 — clarify once, then escalate
async def test_second_consecutive_miss_escalates(agent: Agent, llm: ScriptedLLM) -> None:
    llm.queue(LLMResponse(text="no idea"), LLMResponse(text="still no idea"))
    first = await _send(agent, "how long do returns take")
    second = await _send(agent, "what about international returns")
    assert first.kind == "clarify"
    assert second.kind == "escalated"
    assert second.escalation_reason is EscalationReason.repeated_failure


async def test_a_good_answer_resets_the_miss_counter(agent: Agent, llm: ScriptedLLM) -> None:
    llm.queue(
        LLMResponse(text="no idea"),
        LLMResponse(text="30 days. [S1]"),
        LLMResponse(text="no idea again"),
    )
    assert (await _send(agent, "q1")).kind == "clarify"
    assert (await _send(agent, "q2")).kind == "answer"
    assert (await _send(agent, "q3")).kind == "clarify"


# 8 — frustration
async def test_two_negative_messages_escalate(agent: Agent, llm: ScriptedLLM) -> None:
    llm.queue(
        LLMResponse(text="Shipping is free over ₹1,999. [S1]"),  # no frustration cue: no check
        LLMResponse(text="NEGATIVE"),  # "annoying" -> current message
        LLMResponse(text="NEUTRAL"),  # previous message wasn't angry, so no escalation yet
        LLMResponse(text="Sorry about that. [S1]"),
        LLMResponse(text="NEGATIVE"),  # current
        LLMResponse(text="NEGATIVE"),  # previous -> streak
    )
    await _send(agent, "how much is shipping")
    await _send(agent, "this is annoying")
    reply = await _send(agent, "this is still terrible, useless bot")
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.negative_sentiment


async def test_sentiment_failure_does_not_block_the_answer(agent: Agent, llm: ScriptedLLM) -> None:
    llm.queue(LLMResponse(text="30 days. [S1]"))
    await _send(agent, "how long do returns take")
    llm.queue(LLMError("RateLimitError"), LLMResponse(text="Still 30 days. [S1]"))
    assert (await _send(agent, "this is annoying, how long do returns take")).kind == "answer"


# Tool behaviour
async def test_verified_order_lookup_is_answered(
    agent: Agent, llm: ScriptedLLM, seeded_orders: Session
) -> None:
    from app.orders.models import Order

    order = seeded_orders.scalars(select(Order)).first()
    assert order is not None
    llm.queue(
        LLMResponse(
            tool_calls=(
                ToolCall(
                    "t1",
                    "get_order_status",
                    {"order_number": order.number, "email": order.customer.email},
                ),
            )
        ),
        LLMResponse(text=f"Your order {order.number} is {order.status.value}."),
    )
    reply = await _send(agent, f"where is my order {order.number}")
    assert reply.kind == "answer"  # grounded by the tool, no citation needed
    assert order.number in reply.text
    tool_result = llm.calls[-1]["messages"][-1]["content"][0]["content"]
    assert order.number in tool_result


async def test_wrong_email_leaks_nothing(
    agent: Agent, llm: ScriptedLLM, seeded_orders: Session
) -> None:
    from app.orders.models import Order

    order = seeded_orders.scalars(select(Order)).first()
    assert order is not None
    llm.queue(
        LLMResponse(
            tool_calls=(
                ToolCall(
                    "t1",
                    "get_order_status",
                    {"order_number": order.number, "email": "attacker@example.com"},
                ),
            )
        ),
        LLMResponse(text="I couldn't verify those details — please check the email used. [S1]"),
    )
    reply = await _send(agent, f"status of {order.number}")
    assert reply.kind == "answer"
    forbidden = [order.customer.email, order.customer.address, order.payment_last4]
    forbidden += [i.product.name for i in order.items]
    if order.tracking_number:
        forbidden.append(order.tracking_number)
    for secret in forbidden:
        assert secret not in llm.context_text
        assert secret not in reply.text


async def test_lockout_escalates(agent: Agent, llm: ScriptedLLM, seeded_orders: Session) -> None:
    for _ in range(6):
        llm.queue(
            LLMResponse(
                tool_calls=(
                    ToolCall(
                        "t1", "get_order_status", {"order_number": "NW-000000", "email": "a@b.c"}
                    ),
                )
            ),
            LLMResponse(text="I couldn't verify that. [S1]"),
        )
    reply = AgentReply("silent")
    for _ in range(6):
        reply = await _send(agent, "where is my order NW-000000")
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.lookup_locked


async def test_model_escalate_tool(agent: Agent, llm: ScriptedLLM) -> None:
    llm.queue(
        LLMResponse(
            tool_calls=(
                ToolCall(
                    "t1",
                    "escalate",
                    {"reason": "restricted_action", "summary": "Wants a bulk-order quote."},
                ),
            )
        )
    )
    reply = await _send(agent, "we need 40 units for corporate gifting next week")
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.restricted_action


async def test_malformed_tool_input_escalates(agent: Agent, llm: ScriptedLLM) -> None:
    llm.queue(LLMResponse(tool_calls=(ToolCall("t1", "get_order_status", {"email": "a@b.c"}),)))
    reply = await _send(agent, "how long do returns take")
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.low_confidence


async def test_unknown_tool_escalates(agent: Agent, llm: ScriptedLLM) -> None:
    llm.queue(LLMResponse(tool_calls=(ToolCall("t1", "delete_order", {"id": 1}),)))
    assert (await _send(agent, "how long do returns take")).kind == "escalated"


async def test_tool_loop_limit_escalates(agent: Agent, llm: ScriptedLLM) -> None:
    for _ in range(MAX_TOOL_ROUNDS):
        llm.queue(LLMResponse(tool_calls=(ToolCall("t1", "get_product", {"query": "towel"}),)))
    reply = await _send(agent, "how much is a towel")
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.low_confidence


# Error handling
async def test_model_error_escalates_with_a_friendly_message(
    agent: Agent, llm: ScriptedLLM
) -> None:
    llm.queue(LLMError("APITimeoutError"))
    reply = await _send(agent, "how long do returns take")
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.low_confidence
    assert "something went wrong" in reply.text.lower()


async def test_transcript_records_customer_and_agent_turns(
    agent: Agent, session: Session, llm: ScriptedLLM
) -> None:
    llm.queue(LLMResponse(text="30 days. [S1]"))
    await _send(agent, "how long do returns take")
    from app.handoff.models import Conversation

    conv = session.get(Conversation, CID)
    assert conv is not None
    assert [(m.role, m.text) for m in conv.messages] == [
        (Role.customer, "how long do returns take"),
        (Role.agent, "30 days."),
    ]


async def test_order_lookup_produces_a_card_and_follow_ups(
    agent: Agent, llm: ScriptedLLM, seeded_orders: Session
) -> None:
    """The card is built from the DTO, so it holds data the model never wrote."""
    from app.agent.blocks import OrderCardBlock, QuickRepliesBlock
    from app.orders.models import Order

    order = seeded_orders.scalars(select(Order)).first()
    assert order is not None
    llm.queue(
        LLMResponse(
            tool_calls=(
                ToolCall(
                    "t1",
                    "get_order_status",
                    {"order_number": order.number, "email": order.customer.email},
                ),
            )
        ),
        LLMResponse(text="Here are the details."),
    )
    reply = await _send(agent, f"where is my order {order.number}")
    cards = [b for b in reply.blocks if isinstance(b, OrderCardBlock)]
    assert len(cards) == 1
    assert cards[0].number == order.number
    assert cards[0].status == order.status.value
    assert [b for b in reply.blocks if isinstance(b, QuickRepliesBlock)]


async def test_failed_lookup_produces_no_card(
    agent: Agent, llm: ScriptedLLM, seeded_orders: Session
) -> None:
    from app.agent.blocks import OrderCardBlock
    from app.orders.models import Order

    order = seeded_orders.scalars(select(Order)).first()
    assert order is not None
    llm.queue(
        LLMResponse(
            tool_calls=(
                ToolCall(
                    "t1",
                    "get_order_status",
                    {"order_number": order.number, "email": "attacker@example.com"},
                ),
            )
        ),
        LLMResponse(text="I couldn't verify those details. [S1]"),
    )
    reply = await _send(agent, f"status of {order.number}")
    assert not [b for b in reply.blocks if isinstance(b, OrderCardBlock)]


async def test_clarify_offers_suggestions(agent: Agent, kb: FakeKB) -> None:
    from app.agent.blocks import QuickRepliesBlock

    kb.hits = [_hit("returns-policy.md", 0.1)]
    reply = await _send(agent, "something unrelated entirely")
    assert reply.kind == "clarify"
    chips = [b for b in reply.blocks if isinstance(b, QuickRepliesBlock)]
    assert chips and chips[0].options
