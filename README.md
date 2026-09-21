# Northwind Goods — Support Agent

An AI support agent for a small e-commerce store. It answers customer questions from the store's
own policy documents, looks up order status for verified customers, and hands over to a human when
it isn't confident or the request is out of bounds.

Built with an automated pipeline: [agent-skills](https://github.com/addyosmani/agent-skills)
(spec → plan → build), [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
(code graph for the coding agent) and
[open-code-review](https://github.com/alibaba/open-code-review) (AI review on every PR).

## Status

| Module | State |
|---|---|
| `orders` — demo store data, verified order lookup, lockout | done |
| `knowledge-base` — 33 policy docs, hybrid keyword + vector search | done |
| `handoff` — tickets, conversation modes, staff UI | done |
| `agent-core` — Claude agent, tools, escalation, HTTP API | done |
| `evals` — golden test set and scoring | next |
| `channel-webchat` / `channel-email` / `channel-whatsapp` | not started |

Branches are stacked: `main` → `feat/orders` → `feat/knowledge-base` → `feat/handoff` →
`feat/agent-core`. `feat/agent-core` has everything.

## Run it

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install dependencies
cp .env.example .env          # then set ANTHROPIC_API_KEY and ANTHROPIC_MODEL
uv run python -m app.seed     # demo store data + document ingest (downloads the embedding model)
uv run uvicorn app.main:app --reload
```

- Staff queue: http://127.0.0.1:8000/staff (user `staff`, password from `.env`)
- Health: http://127.0.0.1:8000/healthz
- Ask the agent something:

```bash
curl -s localhost:8000/v1/messages -H 'content-type: application/json' \
  -d '{"conversation_id":"demo-1","text":"how long do I have to return something?"}'
```

Without an embedding model (no internet, or Hugging Face blocked) the knowledge base falls back to
keyword-only search and everything still runs.

## Checks

```bash
uv run pytest -q                 # 135 tests; the slow/live ones skip without a model or API key
uv run pytest -q -m slow         # search quality with the real embedding model
uv run pytest -q -m live         # two calls to the real Claude API
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

## Layout

```
app/orders/           demo store data + read-only, verified lookups
app/knowledge_base/   markdown ingest, BM25 + vector search
app/handoff/          tickets, conversation state, staff UI
app/agent/            Claude client, policy checks, tools, pipeline, HTTP API
data/docs/            the store's policies (the agent's source of truth)
spec/                 module specs · tasks/  build plans · docs/adr.md  design decisions
```

`CLAUDE.md` tells coding agents how to work in this repo.

## Safety rules that are enforced by tests

- Order data is returned only when the order number and the checkout email match the same order;
  a mismatch and a non-existent order are indistinguishable.
- The agent never refunds, cancels, returns or changes payment — those always go to a human.
- Answers must cite a source or a tool result, otherwise the agent asks to clarify and then hands over.
