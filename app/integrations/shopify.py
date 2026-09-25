"""Shopify connector: live order status and product lookups.

Credentials are a per-store custom app token — the owner creates it in their admin in two minutes,
no app review. Read-only scopes: read_orders, read_products.
"""

import logging
from typing import Any

import httpx

from app.agent.llm import ToolSchema
from app.integrations.base import Cache, ConnectorError, Credentials, Health, Status, registry
from app.orders.schemas import OrderLine, OrderStatus, ProductInfo

log = logging.getLogger(__name__)

API_VERSION = "2025-07"
FIELDS = ("token", "store")


class ShopifyConnector:
    name = "shopify"

    def __init__(self, credentials: Credentials, client: httpx.Client | None = None) -> None:
        self.credentials = credentials
        self._client = client
        self._cache = Cache(ttl_seconds=60)
        self._last_error = ""

    # ---- configuration -------------------------------------------------

    @property
    def configured(self) -> bool:
        return not self.credentials.missing(*FIELDS)

    def health(self) -> Health:
        missing = self.credentials.missing(*FIELDS)
        if missing:
            return Health(
                Status.not_configured,
                f"Set SHOPIFY_{missing[0].upper()}"
                + (f" and {len(missing) - 1} more" if len(missing) > 1 else ""),
            )
        if self._last_error:
            return Health(Status.degraded, self._last_error)
        return Health(Status.ok, f"Connected to {self.credentials.get('store')}")

    def tools(self) -> list[ToolSchema]:
        return [
            {
                "name": "get_order_status",
                "description": (
                    "Look up one order in the store. Requires BOTH the order number and the "
                    "email used at checkout; returns data only when they match the same order."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "order_number": {"type": "string"},
                        "email": {"type": "string"},
                    },
                    "required": ["order_number", "email"],
                },
            },
            {
                "name": "get_product",
                "description": "Look up a product's price and stock by name or SKU.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ]

    # ---- requests ------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=10.0)
        return self._client

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise ConnectorError("Shopify is not configured")
        url = f"https://{self.credentials.get('store')}/admin/api/{API_VERSION}/{path}"
        headers = {
            "X-Shopify-Access-Token": self.credentials.get("token"),
            "Accept": "application/json",
        }
        try:
            response = self._http().get(url, params=params, headers=headers)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            self._last_error = f"HTTP {exc.response.status_code} from Shopify"
            log.warning("Shopify request failed: %s", self._last_error)
            raise ConnectorError(self._last_error) from exc
        except (httpx.HTTPError, ValueError) as exc:
            self._last_error = exc.__class__.__name__
            log.warning("Shopify request failed: %s", self._last_error)
            raise ConnectorError(self._last_error) from exc
        self._last_error = ""
        return data

    # ---- reads ---------------------------------------------------------

    def get_order_status(self, order_number: str, email: str) -> OrderStatus | None:
        """Verified lookup. A mismatch and a missing order are indistinguishable to the caller."""
        number = order_number.strip().lstrip("#")
        wanted = email.strip().lower()
        if not number or not wanted:
            return None
        data = self._get("orders.json", {"name": number, "status": "any", "limit": 5})
        for order in data.get("orders", []):
            owner = (order.get("email") or "").strip().lower()
            if owner and owner == wanted:
                return _to_order_status(order)
        return None

    def get_product(self, query: str) -> ProductInfo | None:
        cached = self._cache.get(f"p:{query.lower()}")
        if cached is not None:
            return cached if isinstance(cached, ProductInfo) else None
        data = self._get("products.json", {"title": query, "limit": 5})
        products = data.get("products") or []
        if not products:
            self._cache.put(f"p:{query.lower()}", False)
            return None
        info = _to_product(products[0])
        self._cache.put(f"p:{query.lower()}", info)
        return info


def _to_order_status(order: dict[str, Any]) -> OrderStatus:
    fulfillments = order.get("fulfillments") or []
    tracking = fulfillments[0].get("tracking_number") if fulfillments else None
    carrier = fulfillments[0].get("tracking_company") if fulfillments else None
    status = _status_of(order)
    return OrderStatus(
        number=str(order.get("name") or order.get("order_number") or ""),
        status=status,
        items=[
            OrderLine(name=str(li.get("title", "item")), quantity=int(li.get("quantity", 1)))
            for li in order.get("line_items") or []
        ],
        placed_at=_parse_dt(order.get("created_at")),
        shipped_at=_parse_dt(fulfillments[0].get("created_at")) if fulfillments else None,
        carrier=carrier,
        tracking_number=tracking,
        eta=None,  # Shopify has no ETA; the carrier's tracking page has it
    )


def _status_of(order: dict[str, Any]) -> Any:
    from app.orders.models import OrderStatusEnum

    if order.get("cancelled_at"):
        return OrderStatusEnum.cancelled
    financial = (order.get("financial_status") or "").lower()
    if financial == "refunded":
        return OrderStatusEnum.refunded
    fulfillment = (order.get("fulfillment_status") or "").lower()
    if fulfillment == "fulfilled":
        return OrderStatusEnum.shipped
    return OrderStatusEnum.processing


def _parse_dt(value: Any) -> Any:
    from datetime import datetime

    if not value:
        return datetime(1970, 1, 1)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime(1970, 1, 1)


def _to_product(product: dict[str, Any]) -> ProductInfo:
    variants = product.get("variants") or [{}]
    first = variants[0]
    stock = int(first.get("inventory_quantity") or 0)
    return ProductInfo(
        sku=str(first.get("sku") or product.get("id") or ""),
        name=str(product.get("title", "")),
        category=str(product.get("product_type") or "general"),
        description=_strip_html(str(product.get("body_html") or ""))[:400],
        price=float(first.get("price") or 0),
        in_stock=stock > 0,
        stock=stock,
    )


def _strip_html(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").strip()


registry.register("shopify", lambda creds: ShopifyConnector(creds))
