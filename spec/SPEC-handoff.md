# Spec: handoff

## Objective

When the agent escalates, a human gets a ticket with full context and can take over the
conversation on the same channel. Customer is told clearly that a person will follow up.

## Behaviour

- `create_ticket(conversation_id, reason, summary, channel) -> Ticket`
  - `reason` enum: `low_confidence`, `customer_requested`, `negative_sentiment`,
    `restricted_action` (refund/cancel/payment), `repeated_failure`, `lookup_locked`
  - `summary`: 1–3 sentence agent-written summary of the issue
- Conversation state: `bot` → `waiting_human` → `human` → `closed`. While `waiting_human` or `human`,
  the agent does **not** auto-reply; new customer messages append to the ticket.
- Staff UI (server-rendered, FastAPI + Jinja + HTMX, basic-auth for the demo):
  - Queue: open tickets, sorted by age, with reason badge
  - Ticket view: transcript, agent summary, reply box (reply goes out via the original channel), close
- Channel-agnostic: sends replies through an `OutboundSender` protocol that channels implement.

## Interface

```python
def create_ticket(session, conversation_id: str, reason: EscalationReason,
                  summary: str, channel: Channel) -> Ticket: ...
def conversation_mode(session, conversation_id: str) -> Mode: ...   # bot | waiting_human | human | closed
def staff_reply(session, ticket_id: int, text: str, sender: OutboundSender) -> None: ...
```

## Acceptance criteria

- Escalation flips mode to `waiting_human`; agent-core refuses to answer in that mode (test)
- Staff reply is delivered through the fake sender with the right channel + recipient (test)
- Closing a ticket returns the conversation to `bot` mode
- Queue page renders 100 tickets < 300 ms locally
- Staff routes reject requests without basic-auth (test)

## Out of scope

Staff accounts/roles, SLAs, assignment rules, notifications to staff (email/Slack) — later.
