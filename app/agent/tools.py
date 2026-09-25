"""The tools the model may call, as a registry a vertical draws from.

A tool is registered once with its schema and its implementation. A vertical lists the tools it
wants by name; an unknown name is a startup error, not a surprise at the first customer message.
`escalate` is never listed and can never be switched off — handing over to a person is not a
feature a configuration gets to remove.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.agent import availability
from app.agent.blocks import Block, order_card, product_card
from app.agent.llm import ToolCall, ToolSchema
from app.handoff.models import ENQUIRY_REASONS, EscalationReason
from app.integrations.base import ConnectorError
from app.orders.service import LookupOutcome, get_product, lookup_order
from app.verticals.config import Business, VerticalConfig

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
    vertical: VerticalConfig | None = None  # for tools that read more than the profile


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


# ---------------------------------------------------------------- capture helpers

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ASK_CONTACT = (
    "Ask for an email or phone number the team can reply to, then call this tool again. "
    "Nothing has been passed on yet."
)


def contact_ok(contact: str) -> bool:
    """An email address, or something with enough digits to be a phone number."""
    contact = contact.strip()
    return bool(_EMAIL.match(contact)) or len(re.sub(r"\D", "", contact)) >= 7


def _money(business: Business, amount: float) -> str:
    return f"{business.currency_symbol}{amount:,.0f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ------------------------------------------------------------------ availability


class AvailabilityInput(BaseModel):
    check_in: date
    check_out: date
    guests: int = Field(default=1, ge=1, le=50)


CANT_CHECK = "Couldn't check availability; the customer needs a person to confirm dates."


def _run_check_availability(context: ToolContext, raw: dict[str, object]) -> ToolResult:
    args = AvailabilityInput(**raw)
    config = context.vertical.availability if context.vertical else None
    if config is None:
        return ToolResult(
            "Availability isn't set up.",
            escalate=EscalationReason.low_confidence,
            summary=CANT_CHECK,
        )
    try:
        answers = availability.check(
            config, args.check_in, args.check_out, args.guests, date.today()
        )
    except availability.InvalidStay as exc:
        return ToolResult(str(exc))
    except ConnectorError:
        return ToolResult(
            "The calendar could not be reached.",
            escalate=EscalationReason.low_confidence,
            summary=CANT_CHECK,
        )

    nights = (args.check_out - args.check_in).days
    head = (
        f"Availability for {args.check_in:%d %b %Y} to {args.check_out:%d %b %Y} "
        f"({_plural(nights, 'night')}, {_plural(args.guests, 'guest')}):"
    )
    if not answers:
        largest = max(r.sleeps for r in config.rooms)
        return ToolResult(
            f"{head} no room sleeps {args.guests}; the largest sleeps {largest}.", grounded=True
        )
    lines = []
    for a in answers:
        if a.free:
            rate = a.room.nightly_rate
            lines.append(
                f"- {a.room.name}: available, {_money(context.business, rate)} a night, "
                f"{_money(context.business, rate * nights)} in total."
            )
        else:
            lines.append(f"- {a.room.name}: not available.")
    tail = (
        "This is not a booking and nothing is held. To request it, get the guest's name and an "
        "email or phone number and call booking_enquiry."
    )
    return ToolResult("\n".join([head, *lines, tail]), grounded=True)


register(
    Tool(
        name="check_availability",
        schema={
            "name": "check_availability",
            "description": (
                "Check which rooms are free for a stay, with nightly rates. Dates must be exact "
                "(YYYY-MM-DD); if the customer gives relative dates, ask for exact ones. Never "
                "treat the result as a booking."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "check_in": {"type": "string", "description": "YYYY-MM-DD"},
                    "check_out": {"type": "string", "description": "YYYY-MM-DD"},
                    "guests": {"type": "integer", "minimum": 1},
                },
                "required": ["check_in", "check_out"],
            },
        },
        run=_run_check_availability,
    )
)


# --------------------------------------------------------------- booking enquiry


class BookingEnquiryInput(BaseModel):
    check_in: date
    check_out: date
    guests: int = Field(ge=1, le=50)
    name: str = Field(min_length=1, max_length=100)
    contact: str = Field(min_length=1, max_length=200)
    room: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=500)


def _run_booking_enquiry(context: ToolContext, raw: dict[str, object]) -> ToolResult:
    args = BookingEnquiryInput(**raw)
    if not contact_ok(args.contact):
        return ToolResult(ASK_CONTACT)
    if args.check_out <= args.check_in:
        return ToolResult("The check-out date must be after the check-in date. Ask again.")
    summary = (
        f"Booking enquiry from {args.name.strip()} ({args.contact.strip()}): "
        f"{_plural(args.guests, 'guest')}, {args.check_in.isoformat()} to "
        f"{args.check_out.isoformat()}"
    )
    if args.room.strip():
        summary += f", {args.room.strip()}"
    summary += "."
    if args.notes.strip():
        summary += f" Notes: {args.notes.strip()}"
    return ToolResult(
        "Enquiry passed to the owner.", escalate=EscalationReason.booking_enquiry, summary=summary
    )


register(
    Tool(
        name="booking_enquiry",
        schema={
            "name": "booking_enquiry",
            "description": (
                "Pass a stay request to the owner, who confirms it personally. Needs dates, "
                "number of guests, the guest's name and an email or phone number. This does NOT "
                "book or hold anything; never tell the customer it is booked."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "check_in": {"type": "string", "description": "YYYY-MM-DD"},
                    "check_out": {"type": "string", "description": "YYYY-MM-DD"},
                    "guests": {"type": "integer", "minimum": 1},
                    "name": {"type": "string"},
                    "contact": {"type": "string", "description": "email or phone"},
                    "room": {"type": "string", "description": "optional preferred room"},
                    "notes": {"type": "string", "description": "optional, e.g. arrival time"},
                },
                "required": ["check_in", "check_out", "guests", "name", "contact"],
            },
        },
        run=_run_booking_enquiry,
    )
)


# ------------------------------------------------------------------- escalate


class EscalateInput(BaseModel):
    reason: EscalationReason
    summary: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _not_an_enquiry(cls, value: EscalationReason) -> EscalationReason:
        # An enquiry ticket is only worth something with the details its tool captures.
        if value in ENQUIRY_REASONS:
            raise ValueError("use the capture tool for enquiries")
        return value


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
                    "reason": {
                        "type": "string",
                        "enum": [r.value for r in EscalationReason if r not in ENQUIRY_REASONS],
                    },
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


# A fixed, representative tool set for adapter fixtures: the launch store's. Pinned, so adding a
# tool to the registry doesn't change what those tests send.
TOOLS: list[ToolSchema] = schemas(resolve(["get_order_status", "get_product"]))
