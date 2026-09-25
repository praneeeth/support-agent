"""The tools the model may call, as a registry a vertical draws from.

A tool is registered once with its schema and its implementation. A vertical lists the tools it
wants by name; an unknown name is a startup error, not a surprise at the first customer message.
`escalate` is never listed and can never be switched off — handing over to a person is not a
feature a configuration gets to remove.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.agent.blocks import Block, order_card, product_card
from app.agent.llm import ToolCall, ToolSchema
from app.handoff.models import EscalationReason
from app.orders.service import LookupOutcome, get_product, lookup_order
from app.verticals.config import Business

NOT_VERIFIED = (
    "No order matches those details. Tell the customer you could not verify the order and ask "
    "them to re-check the order number and the email used at checkout. Do not say whether the "
    "order number exists."
)
NO_PRODUCT = "No product matches that. Ask the customer for the product name or SKU."

ESCALATE = "escalate"


@dataclass(frozen=True)
class ToolResult:
    content: str
    grounded: bool = False  # True when it returned real data the answer may rely on
    escalate: EscalationReason | None = None
    summary: str | None = None
    card: Block | None = None  # rendered from the DTO, never from the model's words


@dataclass(frozen=True)
class ToolContext:
    """What a tool needs besides its arguments."""

    session: Session
    conversation_id: str
    business: Business


@dataclass(frozen=True)
class Tool:
    name: str
    schema: ToolSchema
    run: Callable[[ToolContext, dict[str, object]], ToolResult]


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    _REGISTRY[tool.name] = tool
    return tool


def available() -> list[str]:
    return sorted(name for name in _REGISTRY if name != ESCALATE)


class UnknownTool(KeyError):
    """Raised at startup when a vertical asks for a tool nobody registered."""


def resolve(names: list[str]) -> tuple[Tool, ...]:
    """Config names -> tools, with `escalate` always last and always present."""
    out: list[Tool] = []
    for name in names:
        if name == ESCALATE:
            continue  # always included; listing it is harmless
        tool = _REGISTRY.get(name)
        if tool is None:
            raise UnknownTool(
                f"Unknown tool {name!r}. Available: {', '.join(available()) or 'none'}"
            )
        out.append(tool)
    out.append(_REGISTRY[ESCALATE])
    return tuple(out)


def schemas(tools: tuple[Tool, ...]) -> list[ToolSchema]:
    return [t.schema for t in tools]


def run_tool(context: ToolContext, call: ToolCall, tools: tuple[Tool, ...]) -> ToolResult:
    """Execute one call. A tool this vertical did not enable is treated as unknown."""
    tool = next((t for t in tools if t.name == call.name), None)
    if tool is None:
        return ToolResult(
            f"Unknown tool {call.name!r}.",
            escalate=EscalationReason.low_confidence,
            summary="The assistant called an unknown tool; handing over.",
        )
    try:
        return tool.run(context, call.input)
    except ValidationError:
        return ToolResult(
            "Invalid tool input.",
            escalate=EscalationReason.low_confidence,
            summary="The assistant called a tool with invalid input; handing over.",
        )


# ---------------------------------------------------------------- order status


class OrderLookupInput(BaseModel):
    order_number: str = Field(min_length=1, max_length=20)
    email: str = Field(min_length=3, max_length=200)


def _run_order_status(context: ToolContext, raw: dict[str, object]) -> ToolResult:
    args = OrderLookupInput(**raw)
    result = lookup_order(context.session, context.conversation_id, args.order_number, args.email)
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


register(
    Tool(
        name="get_order_status",
        schema={
            "name": "get_order_status",
            "description": (
                "Look up one order. Requires BOTH the order number (NW-123456) and the email "
                "used at checkout. Returns the status only when they match the same order."
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
        run=_run_order_status,
    )
)


# -------------------------------------------------------------------- products


class ProductInput(BaseModel):
    query: str = Field(min_length=1, max_length=120)


def _run_get_product(context: ToolContext, raw: dict[str, object]) -> ToolResult:
    query = ProductInput(**raw).query
    product = get_product(context.session, query)
    if product is None:
        return ToolResult(NO_PRODUCT)
    stock = "in stock" if product.in_stock else "out of stock"
    money = f"{context.business.currency_symbol}{product.price:,.0f}"
    return ToolResult(
        f"{product.name} (SKU {product.sku}): {money}, {stock}. {product.description}",
        grounded=True,
        card=product_card(product),
    )


register(
    Tool(
        name="get_product",
        schema={
            "name": "get_product",
            "description": "Look up a product's price and live stock by SKU or name.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        run=_run_get_product,
    )
)


# ------------------------------------------------------------------- escalate


class EscalateInput(BaseModel):
    reason: EscalationReason
    summary: str = Field(min_length=1, max_length=500)


def _run_escalate(context: ToolContext, raw: dict[str, object]) -> ToolResult:
    args = EscalateInput(**raw)
    return ToolResult("Handed over to a human.", escalate=args.reason, summary=args.summary)


register(
    Tool(
        name=ESCALATE,
        schema={
            "name": ESCALATE,
            "description": (
                "Hand the conversation to a human. Use for refunds, cancellations, returns, "
                "exchanges, payment changes, complaints, or anything the sources do not cover."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "enum": [r.value for r in EscalationReason]},
                    "summary": {
                        "type": "string",
                        "description": "One or two sentences for the human picking this up.",
                    },
                },
                "required": ["reason", "summary"],
            },
        },
        run=_run_escalate,
    )
)


# Kept for callers that want every shipped tool (tests, the OpenAI adapter's fixtures).
TOOLS: list[ToolSchema] = [t.schema for t in _REGISTRY.values()]
