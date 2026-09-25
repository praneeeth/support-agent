# Spec: Support Agent

Status: **Capability map approved 2026-09-18; extended 2026-09-25 with integrations, portals and
verticals.** Module specs live in `spec/SPEC-<module-id>.md`.

## Objective

An AI support agent a small business can put on its website and its WhatsApp number. It answers
customer questions from that business's own documents, looks up a customer's record only when the
customer can verify it, and hands over to a person whenever it isn't confident or the request is
out of bounds.

One engine serves several businesses. Everything specific to one of them — its name, its
documents, its tools, its refusals — lives in `verticals/<id>/`, not in code. The launch vertical
is `northwind`, a fictional home-goods shop.

**Users**
- *Customer* — asks questions on any channel, expects a correct answer or a clear handoff.
- *Support agent* — works the inbox of handed-over conversations and takes over.
- *Owner / admin* — sees what the assistant is doing, what it knows, and what it's connected to.

## Capability map

| Module id | Responsibility | State |
|---|---|---|
| `knowledge-base` | Ingest documents, chunk, embed, hybrid BM25 + vector search | done |
| `orders` | Demo store data + verified, read-only order lookup | done |
| `handoff` | Escalation tickets, conversation modes, staff takeover | done |
| `agent-core` | Retrieve, call tools, answer-or-escalate; channel-agnostic | done |
| `integrations` | Connector + channel framework; Shopify, iCal availability | built, keys not set |
| `channel-webchat` | Embeddable widget with cards and quick replies | done |
| `channel-whatsapp` | WhatsApp Cloud API webhook + send | built, keys not set |
| `agent-portal` | Inbox, filters, conversation view, reply | done |
| `admin-portal` | Overview, knowledge manager, playground, integration health | done |
| `verticals` | One engine, many businesses: config, tools, guardrails per vertical | phase 5 of 7 |
| `evals` | Golden test set; answer correctness, escalation accuracy, leak checks | specced |
| `channel-email` | Inbound webhook + outbound reply | not started |

Specs written: knowledge-base, orders, handoff, agent-core, evals, integrations, verticals.

**What is not yet true**: nothing has run against a real model (only a scripted double and a local
stub), so answer quality is unmeasured; `evals` is the module that fixes that and it is not built.

## Tech stack

- Python 3.11+, FastAPI, Pydantic v2, Uvicorn
- **Provider-agnostic model access.** `LLM_PROVIDER=openai_compatible` (default) covers Ollama
  locally — free — plus Groq, Google AI Studio, OpenRouter, vLLM and LM Studio;
  `LLM_PROVIDER=anthropic` uses the Claude API. No model id is hard-coded.
- SQLite via SQLAlchemy 2.x (Postgres-compatible schema; swap later without code changes)
- Local embeddings: `fastembed` (BAAI/bge-small-en-v1.5) + BM25 (`rank-bm25`), fused with RRF —
  no second API key, and keyword-only search still works when the model can't be downloaded
- Jinja2 + HTMX for the portals; the chat widget is dependency-free JavaScript
- `pyyaml` for vertical configuration
- pytest, pytest-asyncio, ruff, mypy (strict)
- Docker + docker compose; `uv` for dependencies

## Commands

```
Install:   uv sync
Dev:       uv run uvicorn app.main:app --reload
Seed:      uv run python -m app.seed [--vertical northwind]
Test:      uv run pytest -q
Lint:      uv run ruff check . && uv run ruff format --check .
Types:     uv run mypy app
Evals:     uv run python -m evals.run --min-answer 0.90 --min-escalation 0.95   (not built yet)
Docker:    docker compose up --build
```

Running locally: widget demo at `/chat/demo`, agent inbox at `/staff`, admin at `/admin`.

## Project structure

```
app/
  main.py                 FastAPI app factory, router wiring
  config.py               Settings (pydantic-settings, env vars)
  db.py                   Engine/session
  knowledge_base/         ingest.py, search.py, embed.py, models.py
  orders/                 models.py, service.py, schemas.py, seed.py
  handoff/                models.py, service.py, routes.py, sender.py
  agent/                  core.py, tools.py, prompts.py, policy.py, blocks.py,
                          llm.py, openai_compat.py, routes.py
  channels/               base.py, webchat.py, whatsapp.py, static/widget.js
  integrations/           base.py, shopify.py, ical.py
  verticals/              config.py            (the loader; the data lives below)
  portal/                 labels.py, templates/  (shared shell for both portals)
  admin/                  routes.py, analytics.py
verticals/
  northwind/              vertical.yaml + docs/   — one folder per business
evals/                    golden.jsonl, run.py    (not built yet)
tests/                    mirrors app/ layout
spec/                     module specs · tasks/  build plans · docs/adr.md  decisions
```

## Code style

```python
# Typed, small, pure where possible. Services take a Session; routes stay thin.
def get_order_status(session: Session, order_number: str, email: str) -> OrderStatus | None:
    """Return status only if order_number and email match the same order."""
    order = session.scalar(select(Order).where(Order.number == order_number))
    if order is None or order.customer_email.lower() != email.strip().lower():
        return None  # never reveal whether the order number exists
    return OrderStatus.from_model(order)
```

- snake_case modules/functions, PascalCase classes; ruff defaults, line length 100
- No logic in route handlers beyond validation and calling a service
- All config via `app/config.py`; never read `os.environ` elsewhere
- Business wording lives in the vertical config or `app/portal/labels.py`, never in a template
- Log with structured `logging`; never log message bodies at INFO

## Testing strategy

- Unit tests per service, co-located under `tests/<module>/`
- Model calls are behind an interface; unit tests use a scripted fake. Only `evals/` hits a real one
- Coverage target: 85% lines for `app/`
- Safety properties are asserted, not assumed: cross-customer leaks, refusal of restricted actions,
  citation-or-handoff, and — since verticals — that a config can tighten guardrails but never
  loosen them

## Boundaries

- **Always:** write a failing test first; run lint + tests before commit; one commit per task;
  query codebase-memory-mcp (`get_architecture`, `trace_path`, `detect_changes`) before editing
  code you haven't read this session
- **Ask first:** adding a dependency not listed above; schema changes after `orders` ships;
  changing CI workflows; any real external account (email provider, Meta/WhatsApp, hosting)
- **Never:** commit secrets or `.env`; let the agent issue refunds, cancel orders, or take payments;
  return one customer's order data to another; delete or skip failing tests/evals without approval

## Success criteria (whole project)

- Evals: ≥ 90% answer correctness, ≥ 95% escalation accuracy, **0** cross-customer data leaks
- Web chat p95 time-to-first-token < 2s, full reply < 5s (local, demo data)
- A second vertical runs with no code change — only a new folder under `verticals/`
- Every PR passes: lint, types, tests, open-code-review with no unresolved blocking comments

## Open questions

- Hosting target for the public demo (Render / Fly.io / Railway) — decide before selling to anyone
- Email provider (Postmark vs SendGrid inbound parse) — decide before `channel-email`
- Which model judges the eval set when the answering model is a small local one
- Whether background agents (draft reply, knowledge-gap analyst) become a module of their own
