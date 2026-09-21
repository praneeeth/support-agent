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

## ADR-006: The app depends on `LLMClient`, never on the Anthropic SDK
Decision: `app/agent/llm.py` defines `LLMClient`, `LLMResponse`, `ToolCall` and `LLMError`. `AnthropicLLM` adapts the SDK (note: SDK 1.7 uses `httpx2`); `ScriptedLLM` replays queued responses and records every request, which is what the leak tests assert against.

## ADR-007: Deterministic checks bracket the model
Decision: refusals (human request, refund/cancel/return/exchange/payment change) run before the model; citation and tool-grounding checks run after it. The model can only ever pick between "answer from these sources" and "escalate".

## ADR-008: Clarify once, then hand off
Decision: the first low-confidence miss asks the customer to rephrase (no ticket); a second consecutive miss escalates with `repeated_failure`. Resolves the conflict between spec steps 3 and 7.

## ADR-009: Sentiment behind a keyword pre-filter
Decision: a regex decides when a frustration check is worth a model call; Claude then confirms the current and previous customer message. Keeps the cost at roughly one extra call per angry conversation instead of one per message.

## ADR-010: Two retrieval thresholds
Decision: `KB_MIN_SCORE` (0.35) applies to cosine similarity; `KB_MIN_SCORE_KEYWORD` (0.15) applies when the knowledge base has no vectors, because BM25 scores are on a different scale.
