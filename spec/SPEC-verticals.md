# Spec: verticals (turning the support agent into a product platform)

## Objective

One engine, many industries. Everything specific to a business — its documents, its tools, what it
must never do, how it talks — becomes a **vertical config folder**. Standing up a new customer, or a
new industry, means writing config and content, not code.

Commercial shape: two launch verticals (hospitality, D2C e-commerce) and three demo packs
(clinics, real estate, coaching) that can be shown in a day.

## What a vertical is

```
verticals/<id>/
  vertical.yaml        business profile, enabled tools, guardrails, thresholds, copy
  docs/*.md            the knowledge pack (the only source the agent may answer from)
  evals/golden.jsonl   the vertical's own test set
```

Nothing else. If a vertical needs code, that code is a **tool** in the shared registry, usable by
any vertical that enables it.

### vertical.yaml

```yaml
id: northwind
business:
  name: Northwind Goods
  kind: online home-goods store
  location: Pune, India
  currency: INR
  hours: Mon–Sat, 9:00–19:00 IST
  contact: support@northwindgoods.example
channels: [webchat, email, whatsapp]
knowledge:
  docs_dir: verticals/northwind/docs
  min_score: 0.35
  min_score_keyword: 0.15
tools: [get_order_status, get_product]     # `escalate` is always available
guardrails:
  never:                                    # matched before the model is called
    - id: refund_or_cancel
      patterns: ["refund", "cancel my order", "return my", "exchange my"]
      reason: restricted_action
  allow_questions_about: true               # policy questions still get answered
escalation:
  hours_line: "Mon–Sat, 9am–7pm IST"
evals:
  path: verticals/northwind/evals/golden.jsonl
  min_answer: 0.90
  min_escalation: 0.95
```

## Tool registry

`app/agent/tools.py` becomes a registry: `register(name, schema, handler)`. Built-ins:

| Tool | Verticals | Notes |
|---|---|---|
| `get_order_status` | e-commerce | exists; verified lookup |
| `get_product` | e-commerce | exists |
| `check_availability` | hospitality | dates → rooms/rates from the property's config or PMS later |
| `booking_enquiry` | hospitality | captures dates, guests, contact → ticket for the owner |
| `appointment_request` | clinics | captures preferred slot → ticket; never confirms |
| `lead_capture` | real estate, coaching | captures intent + contact → ticket |
| `escalate` | all | always enabled |

Every tool that "books", "confirms" or "schedules" only ever creates a ticket for a human. Nothing
in the platform commits a business to anything.

## Guardrails per vertical

The deterministic pre-checks stay in code; their *patterns* come from config. Each vertical also
declares categories it must refuse outright. Hard requirement for clinics and anything regulated:
clinical questions ("is this dose safe", "do I have X") are never answered from documents — they
escalate, regardless of what the knowledge pack contains.

## Acceptance criteria

- `VERTICAL=northwind` reproduces today's behaviour exactly; the existing 147 tests pass unchanged
- A second vertical (hospitality) runs end to end with no change to `app/agent/core.py`
- `uv run python -m app.seed --vertical seaside-homestay` ingests that vertical's docs only
- Switching vertical changes: system prompt, available tools, guardrail patterns, thresholds,
  escalation copy and eval set — and nothing else
- A vertical with a missing docs folder or an unknown tool name fails at startup with a clear error,
  not at the first customer message
- Clinic pack: a clinical-advice question escalates even when the docs contain a relevant passage (test)

## Out of scope for now

Per-tenant deployment (several businesses in one instance), billing, a config UI. Each customer runs
their own instance until there's a reason not to.
