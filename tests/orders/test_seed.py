import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import init_db, make_engine
from app.orders.models import Customer, Order, OrderStatusEnum, Product
from app.orders.seed import seed_store


def _content_hash(session: Session) -> str:
    h = hashlib.sha256()
    for model in (Product, Customer, Order):
        for row in session.scalars(select(model).order_by(model.id)):
            h.update(
                repr(
                    sorted((k, str(v)) for k, v in vars(row).items() if not k.startswith("_"))
                ).encode()
            )
    return h.hexdigest()


def test_counts_and_statuses(session: Session) -> None:
    seed_store(session, seed=42)
    assert session.scalar(select(func.count(Product.id))) == 50
    assert session.scalar(select(func.count(Customer.id))) == 120
    assert session.scalar(select(func.count(Order.id))) == 200
    statuses = set(session.scalars(select(Order.status).distinct()))
    assert statuses == set(OrderStatusEnum)


def test_order_numbers_format(session: Session) -> None:
    seed_store(session, seed=42)
    for number in session.scalars(select(Order.number)):
        assert number.startswith("NW-") and len(number) == 9 and number[3:].isdigit()


def test_shipped_orders_have_tracking(session: Session) -> None:
    seed_store(session, seed=42)
    shipped = session.scalars(select(Order).where(Order.status == OrderStatusEnum.shipped)).all()
    assert shipped
    for o in shipped:
        assert o.carrier and o.tracking_number and o.eta and o.shipped_at


def test_every_order_has_items(session: Session) -> None:
    seed_store(session, seed=42)
    for o in session.scalars(select(Order)):
        assert 1 <= len(o.items) <= 4


def test_seed_is_deterministic() -> None:
    hashes = []
    for _ in range(2):
        engine = make_engine("sqlite://")
        init_db(engine)
        with sessionmaker(engine)() as s:
            seed_store(s, seed=42)
            hashes.append(_content_hash(s))
    assert hashes[0] == hashes[1]


def test_seed_is_idempotent(session: Session) -> None:
    seed_store(session, seed=42)
    seed_store(session, seed=42)
    assert session.scalar(select(func.count(Order.id))) == 200
