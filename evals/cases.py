"""The golden-set case format: one JSON object per line.

    {"id": "...", "category": "...", "turns": ["...", "..."],
     "expect": {"kind": "answer", "reason": "...", "source_doc": "returns-policy.md",
                "must_include": ["30 days", ["free", "no charge"]],
                "must_not_include": ["refunded"]}}

`expect` is checked against the reply to the *last* turn. In `must_include`, a list means "any one
of these". Matching is case-insensitive. `kind` may be a list when several outcomes are correct
(a wrong-email lookup may clarify, answer "couldn't verify", or hand over).
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.handoff.models import EscalationReason

Kind = Literal["answer", "clarify", "escalated"]
Term = str | list[str]


class Expect(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Kind | list[Kind]
    reason: EscalationReason | None = None
    source_doc: str | None = None
    must_include: list[Term] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(self.kind) if isinstance(self.kind, list) else (self.kind,)

    @property
    def is_escalation(self) -> bool:
        """Scored on the escalation metric: every acceptable outcome is a handover."""
        return self.kinds == ("escalated",)


class Case(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    turns: list[str] = Field(min_length=1)
    expect: Expect


def load_cases(path: Path | str) -> list[Case]:
    """Every line validated; a duplicate id or a malformed line stops the run before it costs."""
    cases: list[Case] = []
    seen: set[str] = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = Case.model_validate(json.loads(line))
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        if case.id in seen:
            raise ValueError(f"{path}:{number}: duplicate id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases
