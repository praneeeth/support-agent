"""Connector framework: one interface, credentials resolved at runtime, failures that fail safe.

A connector is written and tested before anyone has an account. Configure a key later and it
switches on; leave it unset and the whole thing stays off, with `health()` saying why.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.agent.llm import ToolSchema

log = logging.getLogger(__name__)


class Status(StrEnum):
    ok = "ok"
    not_configured = "not_configured"
    degraded = "degraded"
    broken = "broken"


@dataclass(frozen=True)
class Health:
    status: Status
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status in (Status.ok, Status.degraded)


class ConnectorError(RuntimeError):
    """Anything that went wrong talking to a third party. Never reaches the customer verbatim."""


class Connector(Protocol):
    name: str

    def tools(self) -> list[ToolSchema]: ...
    def health(self) -> Health: ...


@dataclass
class Credentials:
    """Per-client secrets. Values come from the environment; nothing is stored in the repo.

    Naming: <CONNECTOR>_<FIELD>, e.g. SHOPIFY_TOKEN, SHOPIFY_STORE, WHATSAPP_TOKEN.
    """

    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str:
        return (self.values.get(key) or "").strip()

    def missing(self, *keys: str) -> list[str]:
        return [k for k in keys if not self.get(k)]

    @classmethod
    def from_env(cls, prefix: str, fields: tuple[str, ...], env: dict[str, str]) -> "Credentials":
        return cls({f: env.get(f"{prefix}_{f}".upper(), "") for f in fields})


class Registry:
    """Connectors a vertical has switched on. Unknown name at startup is an error, not a surprise
    at the first customer message."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[Credentials], Connector]] = {}
        self._live: dict[str, Connector] = {}

    def register(self, name: str, factory: Callable[[Credentials], Connector]) -> None:
        self._factories[name] = factory

    def enable(self, name: str, credentials: Credentials) -> Connector:
        if name not in self._factories:
            known = ", ".join(sorted(self._factories)) or "none"
            raise KeyError(f"Unknown connector {name!r}. Available: {known}")
        connector = self._factories[name](credentials)
        self._live[name] = connector
        return connector

    def get(self, name: str) -> Connector | None:
        return self._live.get(name)

    def tools(self) -> list[ToolSchema]:
        """Tools from every enabled connector that is actually usable."""
        out: list[ToolSchema] = []
        for connector in self._live.values():
            if connector.health().usable:
                out.extend(connector.tools())
        return out

    def health(self) -> dict[str, Health]:
        return {name: c.health() for name, c in self._live.items()}

    @property
    def available(self) -> list[str]:
        return sorted(self._factories)


registry = Registry()


class Cache:
    """Very small TTL cache. Product and availability lookups repeat within a conversation."""

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self.ttl = ttl_seconds
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._items.get(key)
        if hit is None or time.monotonic() - hit[0] > self.ttl:
            return None
        return hit[1]

    def put(self, key: str, value: Any) -> None:
        self._items[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._items.clear()
