"""A vertical is a business the engine serves: its profile, its documents, its tools.

Everything specific to one customer lives in `verticals/<id>/vertical.yaml` and the folder beside
it. The engine reads the config and never hard-codes a shop name, a currency or an order format.

Safety is deliberately *not* configurable here. A vertical can say which tools it enables and what
its refusals sound like, but the mechanism — check before the model runs, hand over rather than
guess, never answer without a source — stays in code, so a careless config cannot loosen it.
"""

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from app.config import Settings

ROOT = Path("verticals")


class Business(BaseModel):
    """How the assistant describes the business, and the facts its copy needs."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(min_length=1, max_length=120)  # "an online home-goods store"
    location: str = Field(default="", max_length=120)  # "Pune, India"
    currency_symbol: str = Field(default="₹", max_length=3)
    hours: str = Field(default="", max_length=120)  # "Mon–Sat, 9am–7pm IST"
    # What a customer quotes to identify their thing: an order, a booking, a case.
    reference_name: str = Field(default="order number", max_length=40)
    reference_format: str = Field(default="", max_length=40)  # "NW-123456"

    @property
    def described(self) -> str:
        """'Northwind Goods, an online home-goods store based in Pune, India'."""
        where = f" based in {self.location}" if self.location else ""
        return f"{self.name}, {self.kind}{where}"


class Refusal(BaseModel):
    """Something this business must never answer, whatever the documents say.

    A clinic's documents may well describe a condition; the assistant still must not tell a
    customer what their symptoms mean. Matching this hands over with the given line.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=60)
    pattern: str = Field(min_length=1, max_length=400)
    reply: str = Field(min_length=1, max_length=400)

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression: {exc}") from exc
        return value


class Claim(BaseModel):
    """Something an *answer* must not say unless a tool returned it in the same turn.

    Checked on the model's reply, after it is written and before the customer sees it. With no
    `requires_tool`, no tool can back it: the answer is always held back and a person takes over.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=60)
    pattern: str = Field(min_length=1, max_length=600)
    requires_tool: str = ""

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression: {exc}") from exc
        return value


class Guardrails(BaseModel):
    """Patterns a vertical *adds*. The built-in guards are always applied as well — this is
    additive by construction, so a config can tighten safety and never loosen it."""

    model_config = ConfigDict(frozen=True)

    # Extra phrasings that mean "the customer is asking us to do something only a person may do".
    restricted: list[str] = Field(default_factory=list)
    # Extra phrasings that mean "this customer wants a person".
    human: list[str] = Field(default_factory=list)
    # Never answer these, even from a document.
    refuse: list[Refusal] = Field(default_factory=list)
    # What a reference looks like, so "where is SS-1234" is recognised as a lookup question.
    reference_pattern: str = ""
    # Phrasings a tool answers (dates, availability), so they skip the retrieval threshold.
    lookup: list[str] = Field(default_factory=list)
    # Extra lines for the prompt's "things you must never do". Appended; never replacing.
    never_say: list[str] = Field(default_factory=list)
    # Things a reply may not claim unless a tool backed them this turn.
    claims: list[Claim] = Field(default_factory=list)

    @field_validator("never_say")
    @classmethod
    def _rules_are_rules(cls, value: list[str]) -> list[str]:
        for rule in value:
            if not rule.strip().startswith("Never") or len(rule) > 300:
                raise ValueError(f"never_say lines start with 'Never' and stay short: {rule!r}")
        return value

    @field_validator("restricted", "human", "lookup")
    @classmethod
    def _all_compile(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{pattern!r} is not a valid regular expression: {exc}") from exc
        return value

    @field_validator("reference_pattern")
    @classmethod
    def _reference_compiles(cls, value: str) -> str:
        if value:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"not a valid regular expression: {exc}") from exc
        return value


class Room(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=80)
    sleeps: int = Field(ge=1, le=50)
    nightly_rate: float = Field(gt=0)


class Blocked(BaseModel):
    """Dates a room is taken. `end` is the check-out day, so it is free that night."""

    model_config = ConfigDict(frozen=True)

    start: date
    end: date
    room: str = ""  # empty means the whole property

    @model_validator(mode="after")
    def _ordered(self) -> "Blocked":
        if self.end <= self.start:
            raise ValueError("blocked end must be after start")
        return self


class Availability(BaseModel):
    """Rooms, rates and closed dates, for a property with no booking system.

    When ICAL_URLS is set, busy dates from those calendars close the whole property too; the
    rooms and rates still come from here.
    """

    model_config = ConfigDict(frozen=True)

    rooms: list[Room] = Field(min_length=1)
    blocked: list[Blocked] = Field(default_factory=list)
    min_nights: int = Field(default=1, ge=1)
    max_nights: int = Field(default=30, ge=1)
    horizon_days: int = Field(default=365, ge=1)  # how far ahead a customer may ask

    @model_validator(mode="after")
    def _consistent(self) -> "Availability":
        if self.max_nights < self.min_nights:
            raise ValueError("max_nights must be at least min_nights")
        names = {r.name for r in self.rooms}
        unknown = [b.room for b in self.blocked if b.room and b.room not in names]
        if unknown:
            raise ValueError(f"blocked dates name unknown rooms: {', '.join(unknown)}")
        return self


class Widget(BaseModel):
    """What the chat widget shows before anyone types. WIDGET_* settings override each field."""

    model_config = ConfigDict(frozen=True)

    greeting: str = Field(default="Hi! How can I help?", max_length=300)
    tagline: str = Field(default="Typically replies instantly", max_length=120)
    accent: str = Field(default="#0f766e", pattern=r"^#[0-9a-fA-F]{6}$")
    suggestions: list[str] = Field(default_factory=list, max_length=6)


class Evals(BaseModel):
    """The vertical's golden set and the bar it must clear. Thresholds can be raised, not lowered
    below the platform floor, so a config can't quietly make the gate easier."""

    model_config = ConfigDict(frozen=True)

    path: str = "evals/golden.jsonl"  # relative to the vertical folder
    min_answer: float = Field(default=0.90, ge=0.90, le=1.0)
    min_escalation: float = Field(default=0.95, ge=0.95, le=1.0)


class VerticalConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=40)
    # A demo pack: a short knowledge pack for showing the platform, not a real business.
    demo: bool = False
    business: Business
    docs_dir: str = Field(min_length=1)
    # Tool names this vertical switches on. `escalate` is always available and need not be listed.
    tools: list[str] = Field(default_factory=list)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    availability: Availability | None = None
    widget: Widget = Field(default_factory=Widget)
    evals: Evals = Field(default_factory=Evals)

    @model_validator(mode="after")
    def _claims_name_enabled_tools(self) -> "VerticalConfig":
        for claim in self.guardrails.claims:
            if claim.requires_tool and claim.requires_tool not in self.tools:
                raise ValueError(
                    f"claim {claim.name!r} requires tool {claim.requires_tool!r}, "
                    "which this vertical does not enable"
                )
        return self

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("vertical id must be a slug: letters, digits, - and _")
        return value


class VerticalNotFound(FileNotFoundError):
    pass


def load_vertical(vertical_id: str, root: Path | str = ROOT) -> VerticalConfig:
    """Read one vertical's config. A missing or malformed file is a startup error, not a surprise
    at the first customer message."""
    folder = Path(root) / vertical_id
    path = folder / "vertical.yaml"
    if not path.is_file():
        available = (
            ", ".join(
                sorted(p.name for p in Path(root).iterdir() if (p / "vertical.yaml").is_file())
            )
            if Path(root).is_dir()
            else "none"
        )
        raise VerticalNotFound(f"No vertical {vertical_id!r} at {path}. Available: {available}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:  # an empty file, not a malformed one
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a mapping")
    raw.setdefault("id", vertical_id)
    # docs_dir is written relative to the vertical folder, so a pack is movable.
    docs = raw.get("docs_dir", "docs")
    raw["docs_dir"] = str(folder / docs)
    evals = dict(raw.get("evals") or {})
    evals["path"] = str(folder / evals.get("path", "evals/golden.jsonl"))
    raw["evals"] = evals
    return VerticalConfig(**raw)


@lru_cache
def get_vertical() -> VerticalConfig:
    from app.config import get_settings

    return load_vertical(get_settings().vertical)


def docs_dir() -> str:
    """The documents for this deployment: the vertical's own, unless DOCS_DIR overrides it."""
    from app.config import get_settings

    return get_settings().docs_dir or get_vertical().docs_dir


@dataclass(frozen=True)
class WidgetCopy:
    brand: str
    tagline: str
    greeting: str
    accent: str
    suggestions: tuple[str, ...]


def widget_copy(settings: "Settings", vertical: VerticalConfig) -> WidgetCopy:
    """The widget's words: a WIDGET_* setting when one is set, otherwise the vertical's own."""
    w = vertical.widget
    raw = settings.widget_suggestions
    suggestions = [s.strip() for s in raw.split("|")] if raw.strip() else list(w.suggestions)
    return WidgetCopy(
        brand=settings.widget_brand or vertical.business.name,
        tagline=settings.widget_tagline or w.tagline,
        greeting=settings.widget_greeting or w.greeting,
        accent=settings.widget_accent or w.accent,
        suggestions=tuple(s for s in suggestions if s),
    )
