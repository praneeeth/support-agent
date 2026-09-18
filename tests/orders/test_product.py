import pytest
from sqlalchemy.orm import Session

from app.orders.seed import seed_store
from app.orders.service import get_product


@pytest.fixture
def seeded(session: Session) -> Session:
    seed_store(session, seed=42)
    return session


def test_by_sku(seeded: Session) -> None:
    p = get_product(seeded, "NW-SKU-0001")
    assert p is not None and p.sku == "NW-SKU-0001"


def test_by_sku_case_insensitive(seeded: Session) -> None:
    p = get_product(seeded, " nw-sku-0002 ")
    assert p is not None and p.sku == "NW-SKU-0002"


def test_by_partial_name(seeded: Session) -> None:
    p = get_product(seeded, "dutch oven")
    assert p is not None and "Dutch Oven" in p.name


def test_by_name_with_finish(seeded: Session) -> None:
    p = get_product(seeded, "cast iron skillet charcoal")
    assert p is not None and p.name == "Cast Iron Skillet - Charcoal"


def test_stock_flag(seeded: Session) -> None:
    p = get_product(seeded, "NW-SKU-0001")
    assert p is not None
    assert p.in_stock == (p.stock > 0)


def test_unknown(seeded: Session) -> None:
    assert get_product(seeded, "quantum toaster") is None
    assert get_product(seeded, "") is None
