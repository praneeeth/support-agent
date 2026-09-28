"""The tools Claude may call, and their execution against tier-1 services."""

from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.agent.blocks import Block, order_card, product_card
from app.agent.llm import ToolCall, ToolSchema
from app.handoff.models import EscalationReason
from app.orders.service import LookupOutcome, get_product, lookup_order

NOT_VERIFIED = (
    "No order matches those details. Tell the customer you could not verify the order and ask "
    "them to re-check the order number and the email used at checkout. Do not say whether the "
    "order number exists."
)
NO_PRODUCT = "No product matches that. Ask the customer for the product name or SKU."


class OrderLookupInput(BaseModel):
    order_number: str = Field(min_length=1, max_length=20)
    email: str = Field(min_length=3, max_length=200)


class ProductInput(BaseModel):
    query: str = Field(min_length=1, max_length=120)


class EscalateInput(BaseModel):
    reason: EscalationReason
    summary: str = Field(min_length=1, max_length=500)


TOOLS: list[ToolSchema] = [
    {
        "name": "get_order_status",
        "description": (
            "Look up one order. Requires BOTH the order number (NW-123456) and the email used "
            "at checkout. Returns the status only when they match the same order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_number": {"type": "string", "description": "e.g. NW-482913"},
                "email": {"type": "string", "description": "email used at checkout"},
            },
            "required": ["order_number", "email"],
        },
    },
    {
        "name": "get_product",
        "description": "Look up a product's price and live stock by SKU or name.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "escalate",
        "description": (
            "Hand the conversation to a human. Use for refunds, cancellations, returns, "
            "exchanges, payment changes, complaints, or anything the sources do not cover."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [r.value for r in EscalationReason],
                },
                "summary": {
                    "type": "string",
                    "description": "One or two sentences for the human picking this up.",
                },
            },
            "required": ["reason", "summary"],
        },
    },
]


@dataclass(frozen=True)
class ToolResult:
    content: str
    grounded: bool = False  # True when it returned real data the answer may rely on
    escalate: EscalationReason | None = None
    summary: str | None = None
    card: Block | None = None  # rendered from the DTO, never from the model's words


def run_tool(session: Session, conversation_id: str, call: ToolCall) -> ToolResult:
    try:
        if call.name == "get_order_status":
            args = OrderLookupInput(**call.input)
            return _order_status(session, conversation_id, args)
        if call.name == "get_product":
            query = ProductInput(**call.input).query
            product = get_product(session, query)
            if product is None:
                return ToolResult(NO_PRODUCT)
            stock = "in stock" if product.in_stock else "out of stock"
            return ToolResult(
                f"{product.name} (SKU {product.sku}): ₹{product.price:,.0f}, {stock}. "
                f"{product.description}",
                grounded=True,
                card=product_card(product),
            )
        if call.name == "escalate":
            args_e = EscalateInput(**call.input)
            return ToolResult(
                "Handed over to a human.", escalate=args_e.reason, summary=args_e.summary
            )
    except ValidationError:
        return ToolResult(
            "Invalid tool input.",
            escalate=EscalationReason.low_confidence,
            summary="The assistant called a tool with invalid input; handing over.",
        )
    return ToolResult(
        f"Unknown tool {call.name!r}.",
        escalate=EscalationReason.low_confidence,
        summary="The assistant called an unknown tool; handing over.",
    )


def _order_status(session: Session, conversation_id: str, args: OrderLookupInput) -> ToolResult:
    result = lookup_order(session, conversation_id, args.order_number, args.email)
    if result.outcome is LookupOutcome.locked:
        return ToolResult(
            "Order lookups are locked for this conversation.",
            escalate=EscalationReason.lookup_locked,
            summary="Too many failed order-verification attempts; needs manual verification.",
        )
    if result.outcome is LookupOutcome.not_found or result.order is None:
        return ToolResult(NOT_VERIFIED)
    o = result.order
    items = ", ".join(f"{i.quantity} x {i.name}" for i in o.items)
    parts = [f"Order {o.number}: status {o.status.value}", f"items: {items}"]
    if o.shipped_at:
        parts.append(f"shipped {o.shipped_at:%d %b %Y} via {o.carrier}")
    if o.tracking_number:
        parts.append(f"tracking {o.tracking_number}")
    if o.eta:
        parts.append(f"estimated delivery {o.eta:%d %b %Y}")
    return ToolResult(". ".join(parts) + ".", grounded=True, card=order_card(o))
