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
| `handoff` — tickets, conversation modes | done |
| Agent portal — inbox with filters and search, conversation view | done |
| Admin portal — overview, knowledge, playground, integrations | done |
| `agent-core` — Claude agent, tools, escalation, HTTP API | done |
| Web chat widget — cards, quick replies, theming, dark mode | done |
| Integrations: Shopify, iCal availability, WhatsApp | built, keys not set |
| `evals` — golden test set and scoring | next |
| `channel-webchat` / `channel-email` / `channel-whatsapp` | not started |

Branches are stacked: `main` → `feat/orders` → `feat/knowledge-base` → `feat/handoff` →
`feat/agent-core`. `feat/agent-core` has everything.

## Run it

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install dependencies
cp .env.example .env          # defaults to a free local model (see below)
uv run python -m app.seed     # demo store data + document ingest (downloads the embedding model)
uv run uvicorn app.main:app --reload
```

### Choosing a model (free by default)

The agent talks to an `LLMClient` interface, so the provider is configuration, not code.

**Free and local — the default.** Install [Ollama](https://ollama.com), then:

```bash
ollama pull qwen3:8b     # ~5 GB; any tool-calling model works
ollama serve
```

`.env` already points at `http://localhost:11434/v1`. No key, no account, nothing leaves your machine.

**Free and hosted.** Same `LLM_PROVIDER=openai_compatible`, different values — Groq, Google AI
Studio or OpenRouter all expose an OpenAI-compatible endpoint and have a free tier. `.env.example`
lists the URLs.

**Paid, best quality.** Set `LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL`.

Smaller models follow the "cite your source" rule less reliably, so expect more clarify-and-hand-off
replies. The safety behaviour does not depend on the model: refunds, cancellations and order
verification are enforced in code, before and after the model runs.

- Demo storefront with the chat widget: http://127.0.0.1:8000/chat/demo
- Agent inbox: http://127.0.0.1:8000/staff (user `staff`, password from `.env`)
- Admin portal: http://127.0.0.1:8000/admin — overview, knowledge, playground, integration health
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
uv run pytest -q                 # 147 tests; the slow/live ones skip without a model or API key
uv run pytest -q -m slow         # search quality with the real embedding model
LIVE_LLM=1 uv run pytest -q -m live   # two calls to whichever provider .env points at
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
`docs/integrations.md` lists every integration and the variables that switch it on.

## Embedding the widget on a client's site

```html
<script src="https://your-host/chat/widget.js" defer></script>
```

That's the whole installation. No build step, no dependencies, no cookies.

A reply arrives as a list of blocks, so the widget renders more than prose:

- **Order card** — status pill, a four-step delivery timeline, items, dates, carrier and a
  "Track package" link built from the carrier's public tracking URL
- **Product card** — price, stock ("Only 3 left"), SKU and category
- **Quick replies** — opening suggestions, and follow-ups after an order card
- **Handoff banner** — visibly different, so the customer knows a person is taking over
- Sources under each answer, timestamps, unread badge, full-screen on a phone

Cards are built in `app/agent/blocks.py` from the same DTOs the orders module returns — the model
never writes a card, so a card cannot claim something the data does not say.

Appearance is configuration, not code: `WIDGET_BRAND`, `WIDGET_ACCENT`, `WIDGET_LOGO_URL`,
`WIDGET_POSITION`, `WIDGET_THEME` (light/dark/auto) and `WIDGET_SUGGESTIONS`. Text on the accent
colour is chosen by luminance, so a pale brand colour still reads.

## Code review without an API key

[open-code-review](https://github.com/alibaba/open-code-review) has a delegation mode: it picks the
files and rules, and your coding agent does the reviewing, so no model credentials are needed.

```bash
npm install -g @alibaba-group/open-code-review
ocr delegate preview           # which files would be reviewed
ocr delegate rule <files...>   # the rules to apply, ready to hand to Claude Code
```

The GitHub Action version posts line-level comments on every PR, and that one does need model
credentials (`OCR_LLM_TOKEN` + `OCR_LLM_URL`, or `ANTHROPIC_API_KEY`). Without them the workflow
skips itself and says so in the run summary, rather than failing.

## Safety rules that are enforced by tests

- Order data is returned only when the order number and the checkout email match the same order;
  a mismatch and a non-existent order are indistinguishable.
- The agent never refunds, cancels, returns or changes payment — those always go to a human.
- Answers must cite a source or a tool result, otherwise the agent asks to clarify and then hands over.
