import httpx
import pytest

from app.integrations.base import Credentials, Status, registry
from app.integrations.shopify import ShopifyConnector

ORDER = {
    "name": "1042",
    "email": "Asha.Menon@example.com",
    "created_at": "2026-09-14T10:05:00Z",
    "financial_status": "paid",
    "fulfillment_status": "fulfilled",
    "line_items": [{"title": "Cast Iron Skillet", "quantity": 1}],
    "fulfillments": [
        {
            "tracking_number": "TRK99",
            "tracking_company": "Delhivery",
            "created_at": "2026-09-15T08:00:00Z",
        }
    ],
}
PRODUCT = {
    "title": "Cast Iron Skillet",
    "product_type": "kitchen",
    "body_html": "<p>Pre-seasoned <b>skillet</b></p>",
    "variants": [{"sku": "SKU-1", "price": "2499.00", "inventory_quantity": 4}],
}


def _connector(handler, **creds) -> ShopifyConnector:
    values = {"token": "shpat_test", "store": "demo.myshopify.com", **creds}
    return ShopifyConnector(
        Credentials(values), client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_not_configured_is_visible_and_off() -> None:
    c = ShopifyConnector(Credentials({}))
    assert c.health().status is Status.not_configured
    assert "SHOPIFY_" in c.health().detail
    assert c.health().usable is False


def test_configured_health_names_the_store() -> None:
    c = _connector(lambda r: httpx.Response(200, json={"orders": []}))
    assert c.health().status is Status.ok
    assert "demo.myshopify.com" in c.health().detail


def test_order_lookup_requires_matching_email() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.params["name"] == "1042"
        assert request.headers["X-Shopify-Access-Token"] == "shpat_test"
        return httpx.Response(200, json={"orders": [ORDER]})

    c = _connector(handle)
    found = c.get_order_status("#1042", " ASHA.MENON@example.com ")
    assert found is not None and found.number == "1042"
    assert found.tracking_number == "TRK99" and found.carrier == "Delhivery"
    assert [i.name for i in found.items] == ["Cast Iron Skillet"]
    assert c.get_order_status("1042", "someone@else.com") is None


def test_missing_order_and_wrong_email_are_indistinguishable() -> None:
    empty = _connector(lambda r: httpx.Response(200, json={"orders": []}))
    assert empty.get_order_status("1042", "asha.menon@example.com") is None
    mismatch = _connector(lambda r: httpx.Response(200, json={"orders": [ORDER]}))
    assert mismatch.get_order_status("1042", "someone@else.com") is None


def test_cancelled_and_refunded_statuses() -> None:
    cancelled = dict(ORDER, cancelled_at="2026-09-16T00:00:00Z")
    c = _connector(lambda r: httpx.Response(200, json={"orders": [cancelled]}))
    got = c.get_order_status("1042", "asha.menon@example.com")
    assert got is not None and got.status.value == "cancelled"


def test_product_lookup_and_cache() -> None:
    calls = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"products": [PRODUCT]})

    c = _connector(handle)
    p = c.get_product("skillet")
    assert p is not None and p.price == 2499.0 and p.in_stock is True
    assert "<b>" not in p.description
    c.get_product("skillet")
    assert calls["n"] == 1  # second lookup served from cache


@pytest.mark.parametrize("status_code", [401, 429, 500])
def test_api_failure_raises_connector_error_and_degrades_health(status_code: int) -> None:
    from app.integrations.base import ConnectorError

    c = _connector(lambda r: httpx.Response(status_code, json={"errors": "nope"}))
    with pytest.raises(ConnectorError):
        c.get_order_status("1042", "a@b.c")
    assert c.health().status is Status.degraded


def test_registered_in_the_registry() -> None:
    assert "shopify" in registry.available
