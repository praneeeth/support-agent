from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.models import Order
from app.orders.seed import seed_store
from app.orders.service import LookupOutcome, lookup_order


def _order(session: Session) -> Order:
    seed_store(session, seed=42)
    order = session.scalars(select(Order)).first()
    assert order is not None
    return order


def test_found(session: Session) -> None:
    order = _order(session)
    result = lookup_order(session, "conv-1", order.number, order.customer.email)
    assert result.outcome is LookupOutcome.found
    assert result.order is not None and result.order.number == order.number


def test_five_failures_allowed_sixth_locked(session: Session) -> None:
    order = _order(session)
    for _ in range(5):
        r = lookup_order(session, "conv-1", order.number, "wrong@example.com")
        assert r.outcome is LookupOutcome.not_found
    r = lookup_order(session, "conv-1", order.number, "wrong@example.com")
    assert r.outcome is LookupOutcome.locked
    assert r.order is None


def test_locked_even_with_correct_details(session: Session) -> None:
    order = _order(session)
    for _ in range(6):
        lookup_order(session, "conv-1", order.number, "wrong@example.com")
    r = lookup_order(session, "conv-1", order.number, order.customer.email)
    assert r.outcome is LookupOutcome.locked
    assert r.order is None


def test_lockout_is_per_conversation(session: Session) -> None:
    order = _order(session)
    for _ in range(6):
        lookup_order(session, "conv-1", order.number, "wrong@example.com")
    r = lookup_order(session, "conv-2", order.number, order.customer.email)
    assert r.outcome is LookupOutcome.found


def test_success_does_not_reset_counter(session: Session) -> None:
    order = _order(session)
    for _ in range(4):
        lookup_order(session, "conv-1", order.number, "wrong@example.com")
    lookup_order(session, "conv-1", order.number, order.customer.email)
    lookup_order(session, "conv-1", order.number, "wrong@example.com")  # 5th failure
    r = lookup_order(session, "conv-1", order.number, "wrong@example.com")  # 6th
    assert r.outcome is LookupOutcome.locked
