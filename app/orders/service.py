"""Read-only order and product lookups. Nothing in this module modifies an order."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orders.models import Order
from app.orders.schemas import OrderLine, OrderStatus


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
