# Implementation Plan: agent-core (Tier 2)

Spec: `spec/SPEC-agent-core.md`. Builds on tier 1 (`orders`, `knowledge-base`, `handoff`).
Tier-1 plan (`tasks/plan.md`) is complete apart from the Docker check and human review.

## Overview

One async function, `handle_message(conversation_id, channel, text) -> AgentReply`, that either
answers from cited sources/tool results or escalates to a human. Exposed over HTTP for channels.
All logic is unit-tested against a **fake Claude client**; one live smoke test runs only when
`ANTHROPIC_API_KEY` is set (CI, or your laptop).

## Architecture decisions

- **`LLMClient` protocol** wraps the Anthropic SDK (`messages.create` with tools). A
  `ScriptedLLM` fake returns queued responses so every branch is testable without the API.
- **Deterministic pre-checks run before Claude** (human request, refund/cancel/payment intent),
  so cheap, certain cases never depend on the model.
- **Claude gets three tools:** `get_order_status(order_number, email)`, `get_product(query)`,
  `escalate(reason, summary)`. Tool inputs are validated with Pydantic; invalid input → escalate.
- **Citations:** retrieved chunks are numbered `[S1]…[S5]`; the answer must cite at least one, or
  use a tool result. Otherwise the reply is converted to a `low_confidence` escalation.
- **Sentiment:** a separate small classification call (max 5 tokens) on each customer message;
  two negatives in a row → escalate.
- **Tool-use loop capped at 4 rounds**; exceeding it → escalate.
- **Streaming:** the SSE endpoint streams the final answer text; escalations are sent as a single event.

New dependency (approving this plan approves it): `anthropic` (listed in SPEC tech stack).

## Task list

### Phase 4: agent-core  → PR #4
- [x] Task 12: LLM client interface, fake client, and system prompt
- [x] Task 13: Pipeline skeleton with mode check and deterministic pre-checks
- [x] Task 14: Retrieval gate, grounded answer, and citation check
- [x] Task 15: Tools (order lookup with lockout, product, escalate) and the tool-use loop
- [x] Task 16: Clarify-then-escalate and negative-sentiment escalation
- [x] Task 17: API-error handling and HTTP endpoints (JSON + SSE)

### Checkpoint D: agent-core
- [ ] Spec steps 1–8 each have a passing unit test with the fake client
- [ ] Verification-failure test: no order data reaches the model's context or the reply
- [ ] Live smoke test (skipped without a key) passes in CI
- [ ] Coverage ≥ 85%, lint + types clean; PR opened

## Task details

**12 — LLM client, fake, prompt.** `app/agent/llm.py` (`LLMClient` protocol, `AnthropicLLM`,
`ScriptedLLM`), `app/agent/prompts.py` (store persona, rules: answer only from sources, cite
`[S#]`, never promise refunds/cancellations, never ask for card/OTP). Acceptance: fake replays
scripted text/tool-use turns; Anthropic adapter builds the correct request (checked with a stub
transport, no network).

**13 — Skeleton + pre-checks.** `app/agent/core.py`, `app/agent/policy.py`. Records the customer
message via `handoff.record_message`; non-bot mode → `silent`. Regex intent checks: human request →
`customer_requested`; refund/cancel/change-payment → `restricted_action`. Acceptance: one test per
branch; escalation creates a ticket with a summary; the customer gets a clear handoff message.

**14 — Retrieval gate + grounded answer.** Top-5 search; best score < `KB_MIN_SCORE` and not an
order-status question → `low_confidence` (no Claude call). Otherwise call Claude with numbered
sources and the last 10 turns. Answer without `[S#]` and without tool use → `low_confidence`.
Acceptance: tests for below-threshold, cited answer (sources returned), uncited answer → escalation.

**15 — Tools + loop.** `app/agent/tools.py`. `get_order_status` goes through
`orders.lookup_order` (lockout → `lookup_locked` escalation); `not_found` returns a neutral
"couldn't verify" message to Claude — never whether the order exists. Acceptance: verified lookup
answer; wrong email → no order fields anywhere in the model's messages or reply; malformed tool
input → escalate; loop > 4 rounds → escalate.

**16 — Clarify-then-escalate + sentiment.** (Decision 2026-09-19: clarify once, then hand off.)
First low-confidence miss → `clarify` reply, no ticket; second consecutive miss → escalate
`repeated_failure`; two consecutive negative messages → `negative_sentiment`. State is derived from
the conversation's stored messages (no new table). Acceptance: tests for both, including the counter
resetting after a good answer.

**17 — Errors + HTTP.** Anthropic timeout/5xx/rate-limit → friendly message + `low_confidence`
escalation, logged without message bodies. `POST /v1/messages` (JSON) and `POST /v1/messages/stream`
(SSE). Acceptance: error-path tests with the fake; endpoint tests for both; live smoke test marked
`live`.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| No API key in the build sandbox, so the real model is untested here | High | Fake client covers logic; live smoke test + evals run in CI once the key is a repo secret |
| Model ignores the citation rule | Med | Deterministic post-check converts uncited answers to escalations |
| `KB_MIN_SCORE` wrong without the real embedding model | Med | Kept configurable; the `evals` module tunes it on real scores |
| Extra sentiment call adds latency | Low | Runs concurrently with retrieval; tiny token budget |

## Open questions

- None blocking. Which Claude model to use is set by `ANTHROPIC_MODEL`; that's your call later.

## Build notes

- The Anthropic SDK (1.7.0) uses `httpx2`, not `httpx`; test transports must come from `httpx2`.
