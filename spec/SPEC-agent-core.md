# Spec: agent-core

## Objective

A single channel-agnostic function that takes a customer message and returns either a grounded
answer or an escalation — never a confident guess.

## API

```python
async def handle_message(conversation_id: str, channel: Channel, text: str) -> AgentReply
# AgentReply = {kind: "answer" | "clarify" | "escalated" | "silent", text: str, sources: list[str],
#               escalation_reason: EscalationReason | None}
```

Also exposed over HTTP for testing and for channels: `POST /v1/messages`, and streaming via
`POST /v1/messages/stream` (SSE).

## Behaviour

1. If `conversation_mode` is not `bot` → return `silent` (message is appended to the ticket).
2. Pre-checks (deterministic, before calling Claude):
   - Explicit human request ("agent", "human", "real person"…) → escalate `customer_requested`
   - Refund/cancel/change-payment intent (keyword + Claude-classified) → escalate `restricted_action`
3. Retrieve top-5 from knowledge-base. If best score < `KB_MIN_SCORE` **and** the question isn't an
   order-status question → a *low-confidence miss* (see step 7).
4. Call Claude with: system prompt (store persona, rules, "answer only from provided sources"),
   last 10 turns, retrieved chunks, and tools `get_order_status`, `get_product`, `escalate`.
5. Claude must either answer citing source ids, or call `escalate` with reason + summary.
6. Post-check: answer with no cited source and no tool result → a *low-confidence miss*.
7. **Clarify once, then hand off** (decided 2026-09-19): the first low-confidence miss returns
   `clarify` (bot asks the customer to rephrase/add detail; no ticket). A second consecutive miss
   escalates with `repeated_failure`. A good answer resets the count. Claude calling `escalate`
   with `low_confidence`, or an API error, still escalates immediately with `low_confidence`.
8. Negative sentiment (Claude-classified, cheap call) on 2 consecutive messages → `negative_sentiment`.

## Config

`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `KB_MIN_SCORE` (default 0.35, tuned by evals),
`MAX_TURNS_CONTEXT` (10), `REPLY_MAX_TOKENS` (500)

## Acceptance criteria

- With a fake Claude client: each of steps 1–8 has a unit test proving the branch
- Tool calls are validated against schemas; a malformed tool call → escalate, never crash
- Prompt + model never see order data for a customer who failed verification (test on tool layer)
- Anthropic API error/timeout → friendly message + escalate `low_confidence`, error logged
- Streaming endpoint emits first token < 2s p95 against the real API (measured in evals, not unit tests)

## Out of scope

Memory across conversations, multilingual, proactive messages.
