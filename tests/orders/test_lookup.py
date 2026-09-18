import inspect
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base
from app.orders import service
from app.orders.models import Customer, Order
from app.orders.schemas import OrderStatus
from app.orders.seed import seed_store
from app.orders.service import get_order_status


@pytest.fixture
def seeded(session: Session) -> Session:
    seed_store(session, seed=42)
    return session


def test_match_returns_status(seeded: Session) -> None:
    order = seeded.scalars(select(Order)).first()
    assert order is not None
    result = get_order_status(seeded, order.number, order.customer.email)
    assert isinstance(result, OrderStatus)
    assert result.number == order.number
    assert result.status == order.status
    assert len(result.items) == len(order.items)


def test_email_is_case_and_whitespace_insensitive(seeded: Session) -> None:
    order = seeded.scalars(select(Order)).first()
    assert order is not None
    assert get_order_status(seeded, order.number, f"  {order.customer.email.upper()} ") is not None


def test_order_number_is_trimmed_and_case_insensitive(seeded: Session) -> None:
    order = seeded.scalars(select(Order)).first()
    assert order is not None
    assert get_order_status(seeded, f" {order.number.lower()} ", order.customer.email) is not None


def test_not_found_and_mismatch_are_indistinguishable(seeded: Session) -> None:
    order = seeded.scalars(select(Order)).first()
    assert order is not None
    assert get_order_status(seeded, "NW-000000", order.customer.email) is None
    assert get_order_status(seeded, order.number, "someone@else.com") is None


def test_empty_inputs_return_none(seeded: Session) -> None:
    order = seeded.scalars(select(Order)).first()
    assert order is not None
    assert get_order_status(seeded, "", order.customer.email) is None
    assert get_order_status(seeded, order.number, "") is None


@settings(max_examples=1000, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=st.data())
def test_property_no_cross_customer_leak(seeded: Session, data: st.DataObject) -> None:
    orders = _all(seeded, Order)
    customers = _all(seeded, Customer)
    order = data.draw(st.sampled_from(orders))
    other = data.draw(st.sampled_from([c for c in customers if c.id != order.customer_id]))
    assert get_order_status(seeded, order.number, other.email) is None


def test_dto_excludes_sensitive_fields() -> None:
    fields = set(OrderStatus.model_fields)
    for forbidden in ("address", "phone", "payment_last4", "email", "customer", "customer_id"):
        assert forbidden not in fields


def test_no_orm_object_escapes(seeded: Session) -> None:
    order = seeded.scalars(select(Order)).first()
    assert order is not None
    result = get_order_status(seeded, order.number, order.customer.email)
    assert result is not None
    for value in _walk(result.model_dump()):
        assert not isinstance(value, Base)


def test_service_has_no_write_functions() -> None:
    public = [
        n for n, _ in inspect.getmembers(service, inspect.isfunction) if not n.startswith("_")
    ]
    forbidden = ("create", "update", "delete", "cancel", "refund", "set_", "add", "remove", "write")
    allowed = {"record_failed_lookup", "reset_failed_lookups"}  # lockout bookkeeping only
    for name in public:
        if name in allowed:
            continue
        assert not name.startswith(forbidden), name
    src = inspect.getsource(service)
    for stmt in ("session.delete(", ".update(", "insert(Order", "Order("):
        assert stmt not in src


_cache: dict[type[Any], list[Any]] = {}


def _all(session: Session, model: type[Any]) -> list[Any]:
    if model not in _cache:
        _cache[model] = list(session.scalars(select(model)))
    return _cache[model]


def _walk(obj: Any) -> Any:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj
