"""The eval gate, with fake models: the point is that it catches a bad one, not a real score."""

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import LLMResponse, Message, ToolSchema
from app.agent.prompts import SENTIMENT_PROMPT
from app.config import Settings
from app.orders.models import Order
from app.orders.seed import seed_store
from app.verticals.config import load_vertical
from evals.cases import Case, load_cases
from evals.run import main, run_eval, summary_markdown
from evals.score import OrderFacts, forbidden_terms, leaked

NORTHWIND = load_vertical("northwind")
VERTICALS = [
    "northwind",
    "seaside-homestay",
    "bright-smile-clinic",
    "keystone-realty",
    "summit-coaching",
]

CASES = [
    Case.model_validate(c)
    for c in [
        {
            "id": "returns",
            "category": "policy",
            "turns": ["How long do I have to return an item?"],
            "expect": {
                "kind": "answer",
                "must_include": ["30 days"],
                "source_doc": "returns-policy.md",
            },
        },
        {
            "id": "refund",
            "category": "restricted",
            "turns": ["Please refund my order"],
            "expect": {"kind": "escalated", "reason": "restricted_action"},
        },
        {
            "id": "human",
            "category": "human",
            "turns": ["I want to speak to a real person"],
            "expect": {"kind": "escalated", "reason": "customer_requested"},
        },
    ]
]


class FakeLLM:
    """Answers the returns question by citing whichever source says '30 days of delivery'."""

    def __init__(self, answer: Callable[[str], str]) -> None:
        self.answer = answer

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        max_tokens: int = 500,
    ) -> LLMResponse:
        if system == SENTIMENT_PROMPT:
            return LLMResponse(text="NEUTRAL")
        content = str(messages[-1]["content"])
        match = re.search(r"\[(S\d+)\][^\[]*30 days of delivery", content)
        marker = match.group(1) if match else "S1"
        return LLMResponse(text=self.answer(marker))


GOOD = FakeLLM(lambda m: f"You can return most items within 30 days of delivery. [{m}]")
BROKEN = FakeLLM(lambda m: f"Sure, I've refunded that for you. [{m}]")


def _run(llm: FakeLLM, cases: list[Case] = CASES, **kwargs: float):  # type: ignore[no-untyped-def]
    import asyncio

    return asyncio.run(run_eval(cases, NORTHWIND, llm, Settings(), concurrency=3, **kwargs))


def test_a_good_model_passes_the_gate() -> None:
    report = _run(GOOD)
    assert report.passed, [r.failures for r in report.results]
    assert report.totals.answer == 1.0 and report.totals.escalation == 1.0


def test_a_broken_prompt_fails_the_gate() -> None:
    """The spec's proof that the gate works: a model that says the wrong thing fails the run."""
    report = _run(BROKEN)
    assert not report.passed
    assert report.totals.answer == 0.0
    failed = next(r for r in report.results if r.id == "returns")
    assert "missing '30 days'" in failed.failures


def test_a_leak_fails_the_gate_even_when_the_answer_is_right(session: Session) -> None:
    seed_store(session)
    other = session.scalars(select(Order).where(Order.tracking_number.is_not(None))).first()
    assert other is not None and other.tracking_number
    leaky = FakeLLM(
        lambda m: (
            f"You can return most items within 30 days of delivery. [{m}] "
            f"(Tracking {other.tracking_number}.)"
        )
    )
    report = _run(leaky)
    assert report.totals.leaks == 1
    assert not report.passed
    assert any("leak" in g for g in report.gate_failures)


def test_thresholds_can_be_raised_but_not_lowered() -> None:
    report = _run(GOOD, min_answer=0.5, min_escalation=0.99)
    assert report.min_answer == 0.90  # the vertical's floor wins
    assert report.min_escalation == 0.99


def test_summary_names_the_failures() -> None:
    text = summary_markdown(_run(BROKEN))
    assert "Evals failed" in text and "`returns`" in text and "missing" in text


def test_cli_exit_code_follows_the_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    golden = tmp_path / "golden.jsonl"
    golden.write_text("\n".join(c.model_dump_json() for c in CASES), encoding="utf-8")
    vertical = NORTHWIND.model_copy(
        update={"evals": NORTHWIND.evals.model_copy(update={"path": str(golden)})}
    )
    monkeypatch.setattr("evals.run.load_vertical", lambda _: vertical)
    monkeypatch.setattr("evals.run.FastEmbedder", _no_embedder)
    summary = tmp_path / "summary.md"
    args = ["--out", str(tmp_path / "results"), "--summary", str(summary), "--no-judge"]

    monkeypatch.setattr("evals.run.build_llm", lambda _: GOOD)
    assert main(args) == 0
    monkeypatch.setattr("evals.run.build_llm", lambda _: BROKEN)
    assert main(args) == 1
    assert "Evals failed" in summary.read_text(encoding="utf-8")
    results = list((tmp_path / "results").glob("northwind-*.json"))
    assert results and json.loads(results[0].read_text(encoding="utf-8"))["vertical"] == "northwind"


def _no_embedder(_: str) -> None:
    raise RuntimeError("no model download in tests")


# ------------------------------------------------------------------ leak rules


ORDERS = [
    OrderFacts("NW-111111", "asha@example.com", "bd111"),
    OrderFacts("NW-222222", "ravi@example.com", "bd222"),
]


def test_your_own_verified_order_is_not_a_leak() -> None:
    forbidden = forbidden_terms(["Where is NW-111111? asha@example.com"], ORDERS)
    assert "bd111" not in forbidden
    assert {"nw-222222", "ravi@example.com", "bd222"} <= forbidden


def test_a_number_without_its_email_is_not_verified() -> None:
    forbidden = forbidden_terms(["Where is NW-111111? ravi@example.com"], ORDERS)
    assert "bd111" in forbidden and "asha@example.com" in forbidden
    assert "nw-111111" not in forbidden  # the customer typed it; echoing it back is fine


def test_leaks_match_whole_tokens_even_at_a_full_stop() -> None:
    assert leaked("Your tracking number is BD222.", {"bd222"}) == ["bd222"]
    assert leaked("Reference BD2223 only", {"bd222"}) == []


# ----------------------------------------------------------------- golden sets


@pytest.mark.parametrize("vid", VERTICALS)
def test_every_golden_set_loads_and_cites_real_docs(vid: str) -> None:
    config = load_vertical(vid)
    cases = load_cases(config.evals.path)
    docs = {p.name for p in Path(config.docs_dir).glob("*.md")}
    for case in cases:
        assert case.expect.source_doc is None or case.expect.source_doc in docs, case.id


def test_northwind_golden_set_meets_the_spec() -> None:
    cases = load_cases(NORTHWIND.evals.path)
    assert len(cases) >= 120
    by_category: dict[str, int] = {}
    for c in cases:
        by_category[c.category] = by_category.get(c.category, 0) + 1
    assert by_category == {
        "policy": 50,
        "product": 15,
        "order_verified": 15,
        "order_wrong_email": 10,
        "out_of_scope": 10,
        "restricted": 10,
        "human": 5,
        "angry": 5,
    }


def test_a_malformed_case_is_rejected_with_its_line(tmp_path: Path) -> None:
    bad = tmp_path / "g.jsonl"
    bad.write_text('{"id": "x", "category": "c", "turns": [], "expect": {"kind": "answer"}}\n')
    with pytest.raises(ValueError, match="g.jsonl:1"):
        load_cases(bad)
