"""DTOs that leave the orders module. Only these types are ever returned to callers."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.orders.models import OrderStatusEnum


class OrderLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    quantity: int


class OrderStatus(BaseModel):
    """Customer-safe view of one order. No address, phone, email or payment data."""

    model_config = ConfigDict(frozen=True)

    number: str
    status: OrderStatusEnum
    items: list[OrderLine]
    placed_at: datetime
    shipped_at: datetime | None
    carrier: str | None
    tracking_number: str | None
    eta: date | None


class ProductInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    name: str
    category: str
    description: str
    price: float
    in_stock: bool
    stock: int
