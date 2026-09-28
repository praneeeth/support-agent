"""Read-only order and product lookups. Nothing in this module modifies an order."""

import enum
import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.orders.models import LookupAttempt, Order, Product
from app.orders.schemas import OrderLine, OrderStatus, ProductInfo


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def get_order_status(session: Session, order_number: str, email: str) -> OrderStatus | None:
    """Return status only if order_number and email belong to the same order.

    Not-found and mismatch return the same None through the same path, so a caller
    can never learn whether an order number exists.
    """
    number = order_number.strip().upper()
    wanted = _normalise_email(email)
    order = session.scalar(select(Order).where(Order.number == number)) if number else None
    owner = _normalise_email(order.customer.email) if order is not None else None
    if order is None or not wanted or owner != wanted:
        return None
    return OrderStatus(
        number=order.number,
        status=order.status,
        items=[OrderLine(name=i.product.name, quantity=i.quantity) for i in order.items],
        placed_at=order.placed_at,
        shipped_at=order.shipped_at,
        carrier=order.carrier,
        tracking_number=order.tracking_number,
        eta=order.eta,
    )


class LookupOutcome(enum.StrEnum):
    found = "found"
    not_found = "not_found"
    locked = "locked"


@dataclass(frozen=True)
class LookupResult:
    outcome: LookupOutcome
    order: OrderStatus | None = None


def lookup_order(
    session: Session,
    conversation_id: str,
    order_number: str,
    email: str,
    max_failures: int | None = None,
) -> LookupResult:
    """Verified lookup with per-conversation lockout after too many failures."""
    limit = max_failures if max_failures is not None else get_settings().max_failed_lookups
    attempt = session.get(LookupAttempt, conversation_id)
    if attempt is not None and attempt.failures >= limit:
        return LookupResult(LookupOutcome.locked)

    status = get_order_status(session, order_number, email)
    if status is not None:
        return LookupResult(LookupOutcome.found, status)

    if attempt is None:
        attempt = LookupAttempt(conversation_id=conversation_id, failures=0)
        session.add(attempt)
    attempt.failures += 1
    session.commit()
    return LookupResult(LookupOutcome.not_found)


def get_product(session: Session, query: str) -> ProductInfo | None:
    """Find a product by SKU, or by the best word-overlap match on its name."""
    q = query.strip()
    if not q:
        return None
    product = session.scalar(select(Product).where(func.upper(Product.sku) == q.upper()))
    if product is None:
        words = {w for w in re.findall(r"[a-z0-9']+", q.lower()) if len(w) > 1}
        best, best_score = None, 0.0
        for p in session.scalars(select(Product)):
            name_words = set(re.findall(r"[a-z0-9']+", p.name.lower()))
            overlap = len(words & name_words)
            score = overlap / len(words) if words else 0.0
            # Require most query words to match; ties go to the lowest SKU (stable).
            if score > best_score:
                best, best_score = p, score
        product = best if best_score >= 0.6 else None
    if product is None:
        return None
    return ProductInfo(
        sku=product.sku,
        name=product.name,
        category=product.category,
        description=product.description,
        price=float(product.price),
        in_stock=product.stock > 0,
        stock=product.stock,
    )
