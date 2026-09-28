"""The second vertical: a homestay, built from config and content alone."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.core import Agent
from app.agent.llm import LLMResponse, ScriptedLLM, ToolCall
from app.channels.webchat import widget_config
from app.config import Settings
from app.handoff.models import Channel, EscalationReason, Ticket
from app.knowledge_base.ingest import CATALOG_PREFIX, ingest_catalog
from app.knowledge_base.models import KbDocument
from app.knowledge_base.search import KnowledgeBase
from app.orders.models import Order
from app.orders.seed import seed_store
from app.seed import seed_all
from app.verticals.config import load_vertical

HOMESTAY = load_vertical("seaside-homestay")
NORTHWIND = load_vertical("northwind")
DOCS = Path(HOMESTAY.docs_dir)
GOLDEN = Path("verticals/seaside-homestay/evals/golden.jsonl")


def test_the_profile_is_the_homestay() -> None:
    assert HOMESTAY.business.name == "Seaside Homestay"
    assert HOMESTAY.tools == ["check_availability", "booking_enquiry"]
    assert HOMESTAY.availability is not None
    assert {r.name for r in HOMESTAY.availability.rooms} == {
        "Sea View Room",
        "Garden Room",
        "Family Cottage",
    }


def test_the_knowledge_pack_has_front_matter_on_every_doc() -> None:
    docs = sorted(DOCS.glob("*.md"))
    assert len(docs) >= 25
    for path in docs:
        head = path.read_text(encoding="utf-8").split("---")[1]
        for field in ("title:", "category:", "updated:"):
            assert field in head, (path.name, field)


def test_the_pack_does_not_leak_the_other_business() -> None:
    for path in DOCS.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "Northwind" not in text and "NW-" not in text, path.name


def test_the_eval_set_is_well_formed() -> None:
    cases = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()]
    assert len(cases) >= 40
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert case["turns"] and case["expect"]["kind"] in {"answer", "clarify", "escalated"}
        doc = case["expect"].get("source_doc")
        assert doc is None or (DOCS / doc).is_file(), doc


def test_seeding_the_homestay_ingests_its_docs_only(session: Session) -> None:
    seed_store(session)  # a northwind seed happened here earlier
    ingest_catalog(session)

    seed_all(session, vertical=HOMESTAY)

    paths = set(session.scalars(select(KbDocument.source_path)))
    assert paths == {p.name for p in DOCS.glob("*.md")}
    assert not any(p.startswith(CATALOG_PREFIX) for p in paths)


def test_a_fresh_homestay_database_gets_no_demo_store(session: Session) -> None:
    seed_all(session, vertical=HOMESTAY)
    assert session.scalar(select(func.count(Order.id))) == 0


def test_the_widget_speaks_for_the_vertical_unless_overridden() -> None:
    config = widget_config(Settings(), HOMESTAY)
    assert config["brand"] == "Seaside Homestay"
    assert config["accent"] == "#0e7490"
    assert "Do you allow dogs?" in config["suggestions"]  # type: ignore[operator]

    overridden = widget_config(Settings(widget_brand="Meera's Place"), HOMESTAY)
    assert overridden["brand"] == "Meera's Place"


def test_northwind_widget_is_unchanged() -> None:
    config = widget_config(Settings(), NORTHWIND)
    assert config["brand"] == "Northwind Goods"
    assert config["greeting"] == "Hi! Ask me about orders, shipping, returns or products."
    assert config["suggestions"] == [
        "Where is my order?",
        "What is your return policy?",
        "Do you ship internationally?",
    ]


@pytest.fixture
def agent(session: Session) -> tuple[Agent, ScriptedLLM]:
    seed_all(session, vertical=HOMESTAY)
    llm = ScriptedLLM()
    agent = Agent(
        session=session,
        kb=KnowledgeBase.load(session),
        llm=llm,
        settings=Settings(),
        vertical=HOMESTAY,
    )
    return agent, llm


async def test_an_enquiry_runs_end_to_end(
    agent: tuple[Agent, ScriptedLLM], session: Session
) -> None:
    bot, llm = agent
    check_in = date.today() + timedelta(days=40)
    check_out = check_in + timedelta(days=2)
    llm.queue(
        LLMResponse(
            tool_calls=(
                ToolCall(
                    "t1",
                    "booking_enquiry",
                    {
                        "check_in": check_in.isoformat(),
                        "check_out": check_out.isoformat(),
                        "guests": 2,
                        "name": "Rahul",
                        "contact": "rahul@example.com",
                    },
                ),
            )
        )
    )
    reply = await bot.handle_message(
        "hs-1", Channel.webchat, "I'd like to book 2 nights for two, how does booking work?"
    )
    assert reply.kind == "escalated"
    assert reply.escalation_reason is EscalationReason.booking_enquiry
    assert "isn't a confirmed booking" in reply.text
    assert "Seaside Homestay" in llm.calls[0]["system"]
    assert llm.calls[0]["tools"] == ["check_availability", "booking_enquiry", "escalate"]
    ticket = session.scalar(select(Ticket).where(Ticket.conversation_id == "hs-1"))
    assert ticket is not None and "rahul@example.com" in ticket.summary
