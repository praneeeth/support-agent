"""Deterministic message checks that run before the model is called.

The built-in patterns below always apply. A vertical may add its own — more phrasings that mean
"a person must handle this", and topics it must never answer at all — but it cannot remove these.
Guardrails are additive by construction, so a careless config can fail to catch something new;
it can never switch off what is already caught.
"""

import re
from dataclasses import dataclass

from app.verticals.config import Guardrails

# "put me through to a person" — an explicit request for a human.
_HUMAN = re.compile(
    r"\b(real (person|human)|speak|talk|chat)\b.{0,20}\b(human|person|agent|someone|representative"
    r"|advisor|executive)\b|\b(human|live) (agent|support|person)\b|\bcustomer (care|service) "
    r"(executive|agent|team)\b|\bconnect me\b|\bput me through\b|\bescalate\b",
    re.IGNORECASE,
)

# Actions the bot must never perform: refunds, cancellations, returns/exchanges, payment changes.
_RESTRICTED = re.compile(
    r"\b(cancel|refund|reimburse|return|exchange|replace)\b.{0,30}\b(my|this|the|that|order|item|"
    r"product|purchase|payment|parcel)\b"
    r"|\b(i want|i'd like|i would like|please|can you|could you|need)\b.{0,25}\b(cancel|refund|"
    r"return|exchange|money back|replacement)\b"
    r"|\b(refund|cancel|charge)\b.{0,15}\b(me|it)\b"
    r"|\bchange\b.{0,20}\b(payment|card|billing)\b",
    re.IGNORECASE,
)

# Questions *about* policy are answerable from the docs, so they must not trip _RESTRICTED.
_POLICY_QUESTION = re.compile(
    r"\b(policy|policies|how long|how many days|what is|what's|whats|do you (offer|accept|allow)"
    r"|can i still|is it possible|window|eligible|terms)\b",
    re.IGNORECASE,
)

_ORDER_NUMBER = re.compile(r"\bNW-\d{6}\b", re.IGNORECASE)
_ORDER_QUESTION = re.compile(
    r"\b(where('s| is)? my (order|parcel|package|delivery)|order status|status of my order|"
    r"track(ing)? (my )?(order|parcel|package)|has (it|my order) shipped|when will (it|my order) "
    r"arrive|delivered yet|shipped yet|tracking number)\b",
    re.IGNORECASE,
)


def wants_human(text: str) -> bool:
    return bool(_HUMAN.search(text))


def wants_restricted_action(text: str) -> bool:
    """True for *requests* to refund/cancel/return/exchange, not questions about the policy."""
    if _POLICY_QUESTION.search(text):
        return False
    return bool(_RESTRICTED.search(text))


def is_order_question(text: str) -> bool:
    """Order-status questions are answered by a tool, so they bypass the retrieval threshold."""
    return bool(_ORDER_NUMBER.search(text) or _ORDER_QUESTION.search(text))


# Cheap pre-filter for frustration. The model confirms; this just avoids a call per message.
_FRUSTRATION = re.compile(
    r"\b(terrible|awful|useless|ridiculous|unacceptable|worst|angry|furious|fed up|frustrat\w*|"
    r"disgust\w*|rubbish|pathetic|scam|cheat\w*|horrible|annoying|appalling|nonsense|joke|"
    r"complain\w*|lawyer|consumer court|never again|still waiting|third time|again and again|"
    r"no (one|body) (has )?(replied|responded)|sick of)\b|!{2,}|\?{2,}",
    re.IGNORECASE,
)


def maybe_frustrated(text: str) -> bool:
    """True when the message is worth a sentiment check."""
    return bool(_FRUSTRATION.search(text))


def summarise(text: str, limit: int = 200) -> str:
    """Fallback ticket summary when the model didn't write one."""
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _combine(built_in: re.Pattern[str], extra: list[str]) -> re.Pattern[str]:
    """Built-in patterns OR the vertical's own. Never a replacement."""
    if not extra:
        return built_in
    joined = "|".join(f"(?:{p})" for p in extra)
    return re.compile(f"(?:{built_in.pattern})|(?:{joined})", re.IGNORECASE)


@dataclass(frozen=True)
class Policy:
    """The checks for one vertical."""

    restricted: re.Pattern[str]
    human: re.Pattern[str]
    reference: re.Pattern[str]
    refusals: tuple[tuple[str, re.Pattern[str], str], ...]

    def wants_human(self, text: str) -> bool:
        return bool(self.human.search(text))

    def wants_restricted_action(self, text: str) -> bool:
        if _POLICY_QUESTION.search(text):
            return False
        return bool(self.restricted.search(text))

    def is_order_question(self, text: str) -> bool:
        return bool(self.reference.search(text) or _ORDER_QUESTION.search(text))

    def refusal_for(self, text: str) -> tuple[str, str] | None:
        """(name, reply) for the first topic this business never answers."""
        for name, pattern, reply in self.refusals:
            if pattern.search(text):
                return name, reply
        return None


def build_policy(guardrails: Guardrails) -> Policy:
    reference = (
        re.compile(guardrails.reference_pattern, re.IGNORECASE)
        if guardrails.reference_pattern
        else _ORDER_NUMBER
    )
    return Policy(
        restricted=_combine(_RESTRICTED, guardrails.restricted),
        human=_combine(_HUMAN, guardrails.human),
        reference=reference,
        refusals=tuple(
            (r.name, re.compile(r.pattern, re.IGNORECASE), r.reply) for r in guardrails.refuse
        ),
    )
