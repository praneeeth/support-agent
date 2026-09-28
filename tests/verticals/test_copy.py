"""Copy follows the business; safety does not.

A vertical can change every word the customer reads. It must not be able to change what the
assistant refuses to do — those rules are asserted here against an invented business.
"""

import pytest

from app.agent.prompts import clarify_text, handoff_text, system_prompt
from app.handoff.models import EscalationReason
from app.verticals.config import Business, load_vertical

NORTHWIND = load_vertical("northwind").business
HOMESTAY = Business(
    name="Seaside Homestay",
    kind="a three-room guest house",
    location="Gokarna, India",
    hours="every day, 8am–8pm IST",
    reference_name="booking reference",
    reference_format="SS-1234",
)


def test_the_prompt_is_about_this_business() -> None:
    prompt = system_prompt(HOMESTAY)
    assert "Seaside Homestay, a three-room guest house based in Gokarna, India" in prompt
    assert "booking reference (format SS-1234)" in prompt
    assert "Northwind" not in prompt and "NW-123456" not in prompt


@pytest.mark.parametrize("business", [NORTHWIND, HOMESTAY, Business(name="X", kind="a shop")])
def test_the_rules_survive_any_business(business: Business) -> None:
    """Whatever a config says, these sentences are in the prompt."""
    prompt = system_prompt(business)
    assert "Answer ONLY from the numbered sources" in prompt
    assert "call the `escalate` tool" in prompt
    assert 'reason "restricted_action"' in prompt
    assert "Never ask for a card number" in prompt
    assert "Never invent policies" in prompt
    assert "never say whether a" in prompt  # no order-enumeration oracle


def test_handoff_copy_carries_the_opening_hours() -> None:
    texts = handoff_text(HOMESTAY)
    assert "(every day, 8am–8pm IST)" in texts[EscalationReason.customer_requested]
    assert "booking reference" in texts[EscalationReason.lookup_locked]


def test_a_business_without_hours_still_reads_as_a_sentence() -> None:
    texts = handoff_text(Business(name="X", kind="a shop"))
    for text in texts.values():
        assert "()" not in text
        assert "  " not in text
        assert text.endswith(".")


def test_every_escalation_reason_has_something_to_say() -> None:
    texts = handoff_text(NORTHWIND)
    assert set(texts) == set(EscalationReason)


def test_clarify_asks_for_the_right_reference() -> None:
    assert "booking reference" in clarify_text(HOMESTAY)
    assert "order number" in clarify_text(NORTHWIND)
