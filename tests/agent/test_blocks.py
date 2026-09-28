"""Cards are built from DTOs, never from the model's prose — these pin that mapping down."""

from datetime import date, datetime

import pytest

from app.agent.blocks import (
    OrderCardBlock,
    ProductCardBlock,
    order_card,
    product_card,
    tracking_url,
)
from app.orders.models import OrderStatusEnum
from app.orders.schemas import OrderLine, OrderStatus, ProductInfo


def _order(status: OrderStatusEnum, **kwargs: object) -> OrderStatus:
    data: dict[str, object] = {
        "number": "NW-100001",
        "status": status,
        "items": [OrderLine(name="Linen Tea Towels", quantity=2)],
        "placed_at": datetime(2026, 8, 30, 10, 0),
        "shipped_at": None,
        "carrier": None,
        "tracking_number": None,
        "eta": None,
    }
    data.update(kwargs)
    return OrderStatus(**data)  # type: ignore[arg-type]


def _product(stock: int) -> ProductInfo:
    return ProductInfo(
        sku="NW-SKU-0011",
        name="Wool Throw Blanket",
        category="bedding",
        description="Warm.",
        price=11739.0,
        in_stock=stock > 0,
        stock=stock,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (OrderStatusEnum.processing, ["done", "current", "pending", "pending"]),
        (OrderStatusEnum.shipped, ["done", "done", "current", "pending"]),
        (OrderStatusEnum.delivered, ["done", "done", "done", "current"]),
    ],
)
def test_timeline_matches_status(status: OrderStatusEnum, expected: list[str]) -> None:
    card = order_card(_order(status))
    assert [s.state for s in card.steps] == expected


@pytest.mark.parametrize("status", [OrderStatusEnum.cancelled, OrderStatusEnum.refunded])
def test_no_journey_for_a_dead_order(status: OrderStatusEnum) -> None:
    """A cancelled order has no position on the delivery journey, so it shows none."""
    card = order_card(_order(status))
    assert card.steps == []
    assert card.status_label in {"Cancelled", "Refunded"}


def test_card_carries_the_order_data() -> None:
    card = order_card(
        _order(
            OrderStatusEnum.shipped,
            carrier="Blue Dart",
            tracking_number="TRK123",
            eta=date(2026, 9, 9),
        )
    )
    assert isinstance(card, OrderCardBlock)
    assert card.number == "NW-100001"
    assert card.placed_on == "30 Aug 2026"
    assert card.eta == "09 Sep 2026"
    assert card.items[0].quantity == 2
    assert card.tracking_url == "https://www.bluedart.com/tracking?awb=TRK123"


@pytest.mark.parametrize(
    ("carrier", "number"),
    [("Aardvark Logistics", "TRK1"), (None, "TRK1"), ("Blue Dart", None), ("Blue Dart", "")],
)
def test_no_link_when_we_cannot_build_one(carrier: str | None, number: str | None) -> None:
    assert tracking_url(carrier, number) is None


def test_tracking_number_is_url_encoded() -> None:
    url = tracking_url("FedEx", "TRK 1/2")
    assert url is not None
    assert " " not in url and "TRK%201%2F2" in url


@pytest.mark.parametrize(
    ("stock", "label"), [(0, "Out of stock"), (3, "Only 3 left"), (40, "In stock")]
)
def test_stock_label(stock: int, label: str) -> None:
    card = product_card(_product(stock))
    assert isinstance(card, ProductCardBlock)
    assert card.stock_label == label
    assert card.in_stock is (stock > 0)


def test_price_is_formatted_for_the_customer() -> None:
    assert product_card(_product(4)).price_label == "₹11,739"
