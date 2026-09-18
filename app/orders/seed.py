"""Deterministic demo data for Northwind Goods."""

import random
from datetime import datetime, timedelta

from faker import Faker
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.orders.models import Customer, Order, OrderItem, OrderStatusEnum, Product

N_PRODUCTS = 50
N_CUSTOMERS = 120
N_ORDERS = 200
BASE_DATE = datetime(2026, 8, 1, 9, 0, 0)

CATALOG: dict[str, list[str]] = {
    "kitchen": [
        "Cast Iron Skillet",
        "Chef's Knife",
        "Bamboo Cutting Board",
        "Enamel Dutch Oven",
        "Pour-Over Coffee Set",
        "Linen Tea Towels",
        "Ceramic Mixing Bowls",
        "Spice Rack",
    ],
    "bedding": [
        "Linen Duvet Cover",
        "Percale Sheet Set",
        "Wool Throw Blanket",
        "Down Pillow",
        "Cotton Quilt",
        "Silk Pillowcase",
    ],
    "bath": [
        "Turkish Towel Set",
        "Waffle Bathrobe",
        "Teak Bath Mat",
        "Stoneware Soap Dish",
        "Organic Cotton Bath Sheet",
    ],
    "decor": [
        "Handwoven Wall Hanging",
        "Stoneware Vase",
        "Brass Candle Holder",
        "Jute Area Rug",
        "Linen Cushion Cover",
        "Oak Picture Frame",
    ],
    "outdoor": [
        "Acacia Serving Tray",
        "Outdoor String Lights",
        "Rattan Planter",
        "Cotton Picnic Blanket",
    ],
}
FINISHES = ["Natural", "Charcoal", "Sage", "Oat", "Terracotta", "Indigo"]
CARRIERS = ["Blue Dart", "Delhivery", "DHL Express", "FedEx"]
PRICE_RANGE: dict[str, tuple[int, int]] = {
    "kitchen": (15, 180),
    "bedding": (40, 260),
    "bath": (18, 120),
    "decor": (12, 150),
    "outdoor": (20, 110),
}


def _products(rng: random.Random) -> list[Product]:
    bases = [(cat, name) for cat, names in CATALOG.items() for name in names]
    products: list[Product] = []
    i = 0
    while len(products) < N_PRODUCTS:
        cat, base = bases[i % len(bases)]
        finish = FINISHES[(i // len(bases)) % len(FINISHES)]
        lo, hi = PRICE_RANGE[cat]
        products.append(
            Product(
                sku=f"NW-SKU-{len(products) + 1:04d}",
                name=f"{base} - {finish}",
                category=cat,
                description=f"{base} in {finish.lower()}. Part of our {cat} collection.",
                price=round(rng.uniform(lo, hi), 2),
                stock=rng.choice([0, 0, 3, 8, 15, 25, 40, 60]),
            )
        )
        i += 1
    return products


def _order_fields(
    rng: random.Random, status: OrderStatusEnum, placed: datetime
) -> dict[str, object]:
    fields: dict[str, object] = {}
    if status in (
        OrderStatusEnum.shipped,
        OrderStatusEnum.delivered,
        OrderStatusEnum.return_requested,
        OrderStatusEnum.refunded,
    ):
        shipped = placed + timedelta(days=rng.randint(1, 3))
        fields.update(
            shipped_at=shipped,
            carrier=rng.choice(CARRIERS),
            tracking_number=f"TRK{rng.randint(10**9, 10**10 - 1)}",
            eta=(shipped + timedelta(days=rng.randint(2, 7))).date(),
        )
    return fields


def seed_store(session: Session, seed: int = 42) -> None:
    """Populate products, customers, orders. No-op if orders already exist."""
    if session.scalar(select(func.count(Order.id))):
        return

    rng = random.Random(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)

    products = _products(rng)
    session.add_all(products)

    customers: list[Customer] = []
    emails: set[str] = set()
    while len(customers) < N_CUSTOMERS:
        name = fake.name()
        local = name.lower().replace(" ", ".").replace("'", "")
        email = f"{local}{rng.randint(1, 99)}@example.com"
        if email in emails:
            continue
        emails.add(email)
        customers.append(
            Customer(name=name, email=email, phone=fake.phone_number(), address=fake.address())
        )
    session.add_all(customers)

    statuses = list(OrderStatusEnum)
    numbers = rng.sample(range(100000, 1000000), N_ORDERS)
    for i in range(N_ORDERS):
        status = statuses[i % len(statuses)] if i < len(statuses) else rng.choice(statuses)
        placed = BASE_DATE + timedelta(days=rng.randint(0, 45), minutes=rng.randint(0, 1439))
        order = Order(
            number=f"NW-{numbers[i]}",
            customer=rng.choice(customers),
            status=status,
            placed_at=placed,
            payment_last4=f"{rng.randint(0, 9999):04d}",
            **_order_fields(rng, status, placed),
        )
        for product in rng.sample(products, rng.randint(1, 4)):
            order.items.append(OrderItem(product=product, quantity=rng.randint(1, 3)))
        session.add(order)

    session.commit()
