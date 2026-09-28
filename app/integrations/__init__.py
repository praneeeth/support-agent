"""Importing the package registers every connector we ship.

Registering is not enabling: a connector appears in the catalogue (and on the admin screen, as
something that can be switched on) but is only constructed when `CONNECTORS` names it and its
credentials are present. Nothing here opens a connection or reads a secret.
"""

from app.integrations import ical, shopify  # noqa: F401 - imported for their side effect
from app.integrations.base import (
    Cache,
    Connector,
    ConnectorError,
    Credentials,
    Health,
    Registry,
    Status,
    registry,
)

__all__ = [
    "Cache",
    "Connector",
    "ConnectorError",
    "Credentials",
    "Health",
    "Registry",
    "Status",
    "registry",
]
