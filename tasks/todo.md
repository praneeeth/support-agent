# Tasks — Tier 1

Commands: tests `uv run pytest -q` · lint `uv run ruff check . && uv run ruff format --check .` ·
types `uv run mypy app`

---

## Task 1: Project skeleton with health endpoint
**Description:** uv project with all tier-1 deps, FastAPI app factory, settings, DB engine/session,
and tool config (ruff, mypy strict on `app/`, pytest + coverage). Docker + compose files.
**Acceptance criteria:**
- [ ] `GET /healthz` returns `{"status":"ok"}` (test)
- [ ] `uv run ruff check .`, `uv run mypy app`, `uv run pytest` all pass
- [ ] `docker compose up --build` serves `/healthz`
**Dependencies:** None
**Files:** `pyproject.toml`, `uv.lock`, `app/main.py`, `app/config.py`, `app/db.py`, `tests/test_health.py`, `Dockerfile`, `compose.yaml`, `.env.example`
**Scope:** M

## Task 2: Store models and deterministic seed
**Description:** Customer, Product, Order, OrderItem models; seed generator with fixed seed:
50 products, 120 customers, 200 orders with all six statuses, `NW-` + 6-digit numbers.
**Acceptance criteria:**
- [ ] Counts match spec; every status present
- [ ] Same seed twice → identical DB content hash (test)
- [ ] Shipped orders have carrier, tracking number, ETA
**Dependencies:** 1
**Files:** `app/orders/models.py`, `app/orders/seed.py`, `tests/orders/test_seed.py`
**Scope:** S

## Task 3: Verified order-status lookup (leak-proof)
**Description:** `get_order_status(session, order_number, email)` returns an `OrderStatus` DTO only
when number and email match the same order. Mismatch and not-found share one code path.
**Acceptance criteria:**
- [ ] Hypothesis property test: 1,000 (order, non-owner email) pairs → always `None`
- [ ] Email match is case- and whitespace-insensitive
- [ ] DTO excludes address/phone/payment/other orders; test asserts no ORM object escapes
- [ ] Test asserts `app/orders/service.py` exposes no write functions
**Dependencies:** 2
**Files:** `app/orders/service.py`, `app/orders/schemas.py`, `tests/orders/test_lookup.py`
**Scope:** S

## Task 4: Lookup lockout and product lookup
**Description:** Failed-lookup counter per `conversation_id`; the 6th failure returns `locked`.
`get_product(session, query)` matches by SKU or fuzzy name and returns price and stock.
**Acceptance criteria:**
- [ ] 5 failures → still allowed; 6th → `locked`; other conversations unaffected
- [ ] `get_product("NW-SKU-0001")` and a partial name both resolve; unknown → `None`
**Dependencies:** 3
**Files:** `app/orders/models.py`, `app/orders/service.py`, `tests/orders/test_lockout.py`, `tests/orders/test_product.py`
**Scope:** S

## Checkpoint A — open PR #1 (orders)

---

## Task 5: Write the demo store's policy and FAQ docs
**Description:** ≥ 30 markdown docs with front-matter for "Northwind Goods". Must contain concrete,
checkable facts: shipping zones/prices/times, 30-day return window with exceptions, warranty terms
per category, payment methods, order-change cutoff, gift cards, international shipping, damaged
items, sizing/care guides, contact hours.
**Acceptance criteria:**
- [ ] ≥ 30 files, each with `title`, `category`, `updated`
- [ ] No two docs contradict each other (review checklist in PR description)
- [ ] 20 search-quality queries drafted, each with its expected doc, in `tests/knowledge_base/queries.yaml`
**Dependencies:** 1
**Files:** `data/docs/*.md`, `tests/knowledge_base/queries.yaml`
**Scope:** M (content-heavy)

## Task 6: Ingest and keyword search, end-to-end
**Description:** Split by heading (≤ ~400 tokens, 50 overlap), content hash per chunk, store in
SQLite; `search()` returns `Hit`s using BM25 only.
**Acceptance criteria:**
- [ ] Ingest of `data/docs` creates chunks; re-ingest with no changes writes 0 rows
- [ ] Editing one doc re-writes only that doc's chunks
- [ ] `search("how long do returns take")` returns the returns doc first
**Dependencies:** 5
**Files:** `app/knowledge_base/models.py`, `app/knowledge_base/ingest.py`, `app/knowledge_base/search.py`, `tests/knowledge_base/test_ingest.py`
**Scope:** M

## Task 7: Vector search and hybrid ranking
**Description:** fastembed embeddings stored as blobs; cosine in numpy; reciprocal-rank fusion with
BM25; normalised score; `Embedder` protocol with a fake for unit tests.
**Acceptance criteria:**
- [ ] Quality test: expected doc in top-3 for ≥ 18/20 queries
- [ ] "what's the weather in Paris" → best score < `KB_MIN_SCORE`
- [ ] Search p95 < 50 ms on the demo corpus
**Dependencies:** 6
**Files:** `app/knowledge_base/embed.py`, `app/knowledge_base/search.py`, `tests/knowledge_base/test_search_quality.py`
**Scope:** M

## Task 8: Index the product catalog and add a one-command seed
**Description:** One chunk per product from `orders`; `python -m app.seed` builds DB, seeds store
data, ingests docs and catalog.
**Acceptance criteria:**
- [ ] 50 product chunks present after seed
- [ ] Product-name query returns that product's chunk first
- [ ] `uv run python -m app.seed` is idempotent
**Dependencies:** 4, 7
**Files:** `app/knowledge_base/ingest.py`, `app/seed.py`, `tests/test_seed_all.py`
**Scope:** S

## Checkpoint B — open PR #2 (knowledge-base)

---

## Task 9: Conversations, tickets and mode state machine
**Description:** `Conversation` (id, channel, customer handle, mode) and `Ticket` (reason enum,
summary, status). `create_ticket` flips mode to `waiting_human`; `conversation_mode` reads it.
**Acceptance criteria:**
- [ ] Allowed transitions only: bot → waiting_human → human → closed → bot; illegal → error (test)
- [ ] New customer message in non-bot mode is appended to the open ticket
**Dependencies:** 1
**Files:** `app/handoff/models.py`, `app/handoff/service.py`, `tests/handoff/test_state.py`
**Scope:** S

## Task 10: Staff reply through an outbound sender, and ticket close
**Description:** `OutboundSender` protocol; `staff_reply` sends via the conversation's channel and
sets mode `human`; closing a ticket returns the conversation to `bot`.
**Acceptance criteria:**
- [ ] Fake sender receives the right channel, recipient and text (test)
- [ ] Close → mode `bot`; replying to a closed ticket → error
**Dependencies:** 9
**Files:** `app/handoff/sender.py`, `app/handoff/service.py`, `tests/handoff/test_reply.py`
**Scope:** S

## Task 11: Staff queue and ticket UI behind basic-auth
**Description:** `/staff` queue (open tickets by age, reason badge) and `/staff/tickets/{id}`
(transcript, summary, reply box, close), Jinja + HTMX, basic-auth from settings.
**Acceptance criteria:**
- [ ] No/invalid credentials → 401 (test)
- [ ] Reply form posts through `staff_reply` (test with fake sender)
- [ ] Queue with 100 tickets renders < 300 ms (test)
**Dependencies:** 10
**Files:** `app/handoff/routes.py`, `app/handoff/templates/*.html`, `tests/handoff/test_routes.py`
**Scope:** M

## Checkpoint C — open PR #3 (handoff), human review before agent-core
