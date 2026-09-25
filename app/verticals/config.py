"""A vertical is a business the engine serves: its profile, its documents, its tools.

Everything specific to one customer lives in `verticals/<id>/vertical.yaml` and the folder beside
it. The engine reads the config and never hard-codes a shop name, a currency or an order format.

Safety is deliberately *not* configurable here. A vertical can say which tools it enables and what
its refusals sound like, but the mechanism — check before the model runs, hand over rather than
guess, never answer without a source — stays in code, so a careless config cannot loosen it.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class VerticalConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=40)
    business: Business
    docs_dir: str = Field(min_length=1)
    # Tool names this vertical switches on. `escalate` is always available and need not be listed.
    tools: list[str] = Field(default_factory=list)

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
    return VerticalConfig(**raw)


@lru_cache
def get_vertical() -> VerticalConfig:
    from app.config import get_settings

    return load_vertical(get_settings().vertical)


def docs_dir() -> str:
    """The documents for this deployment: the vertical's own, unless DOCS_DIR overrides it."""
    from app.config import get_settings

    return get_settings().docs_dir or get_vertical().docs_dir
