# Spec: orders

## Objective

Provide realistic demo store data and a **verified** order-status lookup the agent can call as a tool,
with zero chance of leaking one customer's data to another.

## Data (seeded, deterministic with a fixed random seed)

- 50 products, 120 customers, 200 orders, 1–4 line items each
- Order statuses: `processing`, `shipped` (with carrier + tracking number + ETA), `delivered`,
  `cancelled`, `return_requested`, `refunded`
- Order numbers: `NW-` + 6 digits

## Behaviour

- `get_order_status(order_number, email) -> OrderStatus | None`
  - Returns data only when both match the same order (email case/whitespace-insensitive)
  - Mismatch and not-found return the same `None` — never reveal that an order number exists
  - Rate limit: max 5 failed lookups per conversation, then signal `locked` (agent escalates)
- `get_product(sku_or_name) -> Product | None` — price and stock for catalog questions
- Read-only. No function in this module modifies an order.

## Interface (consumed by agent-core as Claude tools)

```python
def get_order_status(session, order_number: str, email: str) -> OrderStatus | None: ...
def get_product(session, query: str) -> Product | None: ...
```

`OrderStatus` exposes: number, status, items (name, qty), shipped_at, carrier, tracking_number, eta.
It never exposes: address, phone, payment details, other orders of the customer.

## Acceptance criteria

- Seed is deterministic (same seed → identical DB hash)
- Property test: for 1,000 random (order_number, email) pairs where email ≠ owner, result is `None`
- Mismatch and not-found are indistinguishable (same return, same timing within 10 ms)
- 6th failed lookup in one conversation returns `locked`
- No write methods exist in `app/orders/service.py` (asserted by a test that inspects the module)

## Out of scope

Real Shopify/WooCommerce integration (later adapter behind the same interface).
