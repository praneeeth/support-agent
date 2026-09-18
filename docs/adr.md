# Architecture Decisions — Support Agent (tier 1)

## ADR-001: Conversation state is owned by handoff
Context: agent-core must know whether a human owns a conversation.
Decision: `handoff` owns `conversations`, `messages`, `tickets`. Mode machine bot → waiting_human → human → closed → bot.
Consequence: `orders` tracks its own lookup-failure counter keyed by conversation_id, so orders has no dependency on handoff.

## ADR-002: orders is read-only and returns DTOs only
Decision: `app/orders/service.py` has no write paths to orders. `OrderStatus`/`ProductInfo` are the only types that leave the module; no address/phone/email/payment fields. Not-found and email-mismatch share one path and one return value.
Enforcement: property test (1,000 non-owner pairs), DTO field test, source inspection test.

## ADR-003: Hybrid search in-process
Decision: BM25 (title/heading boosted x2) + fastembed bge-small vectors stored as float32 blobs in SQLite, cosine in numpy, reciprocal-rank fusion (k=60). Hit.score = cosine similarity (0–1) for thresholding.
Rationale: < 500 chunks; no vector DB needed. Swap to pgvector with Postgres.
Fallback: keyword-only when vectors are missing (e.g. model download blocked).

## ADR-004: Product catalog lives in the KB without stock
Decision: one KB doc per product under `catalog/<sku>`; stock excluded because it changes constantly — live stock comes from the product tool.

## ADR-005: Staff UI is server-rendered
Decision: Jinja + HTMX (vendored, no CDN), basic-auth with constant-time compare, HX-Request header required on POSTs as a CSRF guard.
