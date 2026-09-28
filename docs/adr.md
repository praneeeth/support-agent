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
Decision: `KB_MIN_SCORE` (0.55; was 0.35, see ADR-027) applies to cosine similarity; `KB_MIN_SCORE_KEYWORD` (0.15) applies when the knowledge base has no vectors, because BM25 scores are on a different scale.

## ADR-011: Provider-agnostic model access
Decision: `OpenAICompatLLM` (app/agent/openai_compat.py) speaks the OpenAI chat API, so Ollama (free, local), Groq, Google AI Studio, OpenRouter, vLLM and LM Studio all work; `LLM_PROVIDER` picks between it and the Anthropic adapter. Messages stay in Anthropic shape internally and are converted at the boundary, including tool_use/tool_result blocks.
Rationale: no paid account required to run or evaluate the agent; the deterministic guards (escalation, citation check, order verification) are model-independent, so a weaker model costs accuracy, never safety.

## ADR-012: Integrations are written before their accounts exist
Decision: every connector and channel adapter is built and tested against mock transports, reads its credentials from configuration at runtime, and reports `not_configured` (with the variable name) until they are set. Its tools are withheld from the model while unconfigured.
Consequence: a client is switched on by pasting a token, not by a code change or a release; and a broken integration degrades to a handoff rather than a wrong answer.

## ADR-013: Hotel availability comes from iCal, not a PMS
Decision: `ical_availability` reads the per-listing calendar feeds that Airbnb, Booking.com and Vrbo already publish; busy periods come from those, everything else is free.
Rationale: small properties rarely run a PMS, and PMS partnerships are slow. One parser covers most of the market with a link the owner copies in one click. A real PMS connector can be added later behind the same interface.

## ADR-014: WhatsApp webhooks are verified and deduplicated in the channel layer
Decision: signature check (HMAC-SHA256) rejects unsigned requests with 401; a `seen_messages` table makes repeated provider deliveries a no-op; the webhook returns 200 immediately and answers on a background task, because Meta retries on delay.

## ADR-015: A reply is a list of blocks, and cards are built in code
Decision: `/chat/message` returns `blocks` — text the model wrote, plus cards (`order_card`, `product_card`) constructed in `app/agent/blocks.py` from the same DTOs the orders module returns, plus `quick_replies`. The model never emits a card and is never asked to format one.
Rationale: an order lookup is structured data; flattening it into a sentence loses the tracking link and makes the customer read a paragraph. Building cards from the DTO also means a card cannot state something the data does not — the prose above it can still be wrong, the card cannot.
Consequence: a new card type is a Pydantic model plus a renderer, not a prompt change. `text` is kept alongside `blocks` so non-visual channels (WhatsApp, email) stay unchanged.

## ADR-016: Widget appearance is configuration, not a fork per client
Decision: brand, tagline, greeting, accent colour, logo, side, light/dark/auto theme and the opening suggestion chips are settings, serialised into the embed script at request time. The widget derives readable text for the accent colour by luminance, and uses CSS custom properties so dark mode is a variable swap.
Rationale: selling the same engine to several clients must not mean a branch per client. One deployment, one `.env`, and the widget looks like the client's site.

## ADR-017: Two portals, one shell, one vocabulary
Decision: the agent portal (`/staff`) and the admin portal (`/admin`) share a Jinja environment, a stylesheet and `app/portal/labels.py`, which translates every enum that reaches a screen — `restricted_action` becomes "Needs authorisation", with a severity that drives its colour.
Rationale: support staff and a client's operations lead are not the audience for the database schema, and two separately-styled internal tools is how an internal tool starts to look unfinished. Keeping the wording in one module means it changes once, and a vertical can override it later without touching templates.

## ADR-018: Admin analytics are computed from the operational tables
Decision: `app/admin/analytics.py` counts conversations, handovers and reasons straight from `conversations`, `messages` and `tickets`. There is no events table and no background aggregation. A conversation counts as escalated once however many tickets it produced.
Consequence: the overview can never disagree with the inbox, and there is nothing extra to deploy or back up. If volume ever makes these queries slow, the fix is an index or a materialised daily roll-up — not a second source of truth.

## ADR-019: The admin portal never handles credentials
Decision: the integrations screen names the environment variable an integration is waiting for and shows its health, and nothing else. No secret is displayed, entered or stored through the browser, and a test asserts a configured token never appears in the rendered page.
Rationale: an operations screen gets screenshotted, shared and screen-shared. Naming `WHATSAPP_TOKEN` is useful; showing its value is a leak waiting to happen.

## ADR-020: A customer is a folder, not a branch
Decision: everything specific to one business — profile, documents, enabled tools, extra guardrails — lives in `verticals/<id>/vertical.yaml` and the folder beside it. `VERTICAL` selects one at startup. The launch store moved to `verticals/northwind/` unchanged, which made the existing suite the regression test for the extraction: Checkpoint E passed with no assertion edited, only fixture paths.
Rationale: selling one engine to several industries must not mean a branch per customer. A new client is a folder and a set of documents.
Consequence: prompts, escalation copy, currency and reference formats are built from the profile; a typo in the tool list raises `UnknownTool` when the Agent is constructed rather than mid-conversation.

## ADR-021: Guardrails are additive and safety stays in code
Decision: a vertical may add restricted-action phrasings, add ways of asking for a human, set its reference format, and declare topics it refuses outright. `build_policy` ORs those with the built-in patterns; it never replaces them. The mechanism — check before the model, hand over rather than guess, answer only with a citation or a tool result — is not configurable at all.
Rationale: configuration is edited by whoever onboards a client, under time pressure. The worst a careless config can do is fail to catch something new; it cannot switch off what is already caught. Tests assert the built-in refusals survive a config that tries to override them.
Consequence: a clinic can refuse to interpret symptoms even though its own documents describe them — the refusal fires before retrieval, so the document never reaches the customer.

## ADR-022: Staff replies are routed by channel, and web chat is pull-based
Decision: the inbox sends through a `ChannelRouter`. Web chat has no push channel, so the widget polls `GET /chat/messages` after a handoff and the stored transcript is the delivery. WhatsApp and email are offered only once configured; a channel that can't deliver returns 503 and nothing is recorded as sent.
Rationale: the inbox used an in-memory recording sender, so no customer ever saw a human reply.

## ADR-023: Availability comes from iCal feeds, else the vertical's config
Decision: `check_availability` reads rooms, rates and closed dates from `vertical.yaml`; when `ICAL_URLS` is set, busy dates from those calendars close the whole property. No PMS integration. A feed that can't be fetched hands over.

## ADR-024: Capture tools file tickets with their own reasons
Decision: `booking_enquiry`, `appointment_request` and `lead_capture` file tickets with new reasons (`booking_enquiry`, `appointment_request`, `lead`). The model's own `escalate` tool can't use them, so every such ticket carries the details its tool captured. Nothing in the platform books, confirms or schedules.

## ADR-025: Claims — a guard on the reply, not just the question
Decision: a vertical may list `claims` its replies can make only when a named tool returned real data that turn (or never). A matching reply is held back and a person takes over. Additive like every guardrail; a claim naming a tool the vertical doesn't enable is a startup error.
Rationale: "no promises about availability the tool didn't return" can't be checked before the model runs.

## ADR-026: Email over Postmark, protected by Basic auth
Decision: Postmark inbound webhook with Basic-auth credentials in the URL (Postmark doesn't sign inbound webhooks); outbound through its API over httpx, no new dependency. One conversation per sender address.

## ADR-027: Retrieval threshold measured, 0.35 → 0.55
Decision: with bge-small, unrelated text commonly scores 0.4–0.55 cosine. Measured 2026-09-28 on every vertical's policy questions against 12 off-topic questions: at 0.35, ~55 of 60 off-topic questions passed the gate; at 0.55, 5 of 60 pass and 1 of ~112 on-topic questions misses (it gets a clarifying question). Replies must still cite a source or hand over, so what slips through is still guarded.
Consequence: re-measure when the embedding model or a vertical's documents change significantly.

## ADR-028: Eval thresholds can be raised, never lowered
Decision: each vertical's `evals:` block sets its bar with a floor of 90% answer / 95% escalation; command-line thresholds can only raise it. Any cross-customer leak fails the run regardless of scores. The optional judge uses a strict one-word verdict because current Claude models reject `temperature`.

