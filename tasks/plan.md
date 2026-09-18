# Implementation Plan: Support Agent — Tier 1 (orders, knowledge-base, handoff)

## Overview

Build the three foundation modules that `agent-core` depends on. None of them calls Claude, so the
whole tier can be built and tested without an API key. Order: skeleton → `orders` (highest risk:
data-leak guarantees, so it goes first) → `knowledge-base` → `handoff`. One PR per module so
open-code-review reviews each one separately.

## Architecture decisions

- **Single SQLite file, SQLAlchemy 2.x (sync).** Schema kept Postgres-compatible (no SQLite-only types).
- **Conversation state lives in `handoff`.** It owns `conversations` + `tickets`. `orders` tracks its
  own failed-lookup counter keyed by `conversation_id`, so `orders` has no dependency on `handoff`.
- **Embeddings stored as float32 blobs; brute-force cosine in numpy.** The demo corpus is under 500 chunks,
  so a vector extension would add setup without improving speed. Swap to pgvector with Postgres.
- **Search = BM25 + vectors merged with reciprocal-rank fusion.** Score normalised 0–1 so
  `agent-core` can apply one threshold.
- **Seed data generated in code with a fixed seed**, with no external data files except `data/docs/*.md`.
- **Staff UI is server-rendered (Jinja + HTMX)**, so there's no frontend build step.

### New dependencies (approving this plan approves these)

Runtime: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `sqlalchemy`, `fastembed`, `rank-bm25`,
`numpy`, `jinja2`, `python-multipart`, `faker`
Dev: `pytest`, `pytest-cov`, `pytest-asyncio`, `hypothesis`, `httpx`, `ruff`, `mypy`

## Task list

### Phase 0: Foundation
- [x] Task 1: Project skeleton with health endpoint

### Phase 1: orders  → PR #1
- [x] Task 2: Store models and deterministic seed
- [x] Task 3: Verified order-status lookup (leak-proof)
- [x] Task 4: Lookup lockout and product lookup

### Checkpoint A: orders (tests, lint, types, coverage 96% — PR pending repo)
- [ ] All tests pass, lint + types clean, coverage ≥ 85%
- [ ] Property test (1,000 cases) shows zero cross-customer leaks
- [ ] PR opened; open-code-review comments resolved

### Phase 2: knowledge-base  → PR #2
- [x] Task 5: Write the demo store's policy and FAQ docs
- [x] Task 6: Ingest and keyword search, end-to-end
- [x] Task 7: Vector search and hybrid ranking
- [ ] Task 8: Index the product catalog and add a one-command seed

### Checkpoint B: knowledge-base
- [ ] Search quality test: correct doc in top-3 for ≥ 18/20 queries
- [ ] Off-topic query scores below threshold; re-ingest writes 0 rows
- [ ] PR opened; open-code-review comments resolved

### Phase 3: handoff  → PR #3
- [ ] Task 9: Conversations, tickets and mode state machine
- [ ] Task 10: Staff reply through an outbound sender, and ticket close
- [ ] Task 11: Staff queue and ticket UI behind basic-auth

### Checkpoint C: Tier 1 complete
- [ ] All three module specs' acceptance criteria met
- [ ] `docker compose up` serves health, staff UI and seeded data
- [ ] `manage_adr` records the tier-1 design decisions in codebase-memory-mcp
- [ ] Human review before starting `agent-core`

Task details: `tasks/todo.md`.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| fastembed model download (~130 MB) slows or breaks CI | Med | Cache `~/.cache/fastembed` in CI; unit tests use a tiny fake embedder, and only the quality test uses the real model |
| Search-quality test flaky | Med | Fixed corpus + fixed queries; deterministic model; threshold asserted on counts, not exact scores |
| Demo docs too thin → later evals meaningless | High | Task 5 has concrete content requirements (facts, numbers, edge cases) that evals will later check |
| Leak via a future code path (not the lookup) | High | `OrderStatus` DTO is the only type that leaves the module; test asserts no ORM objects are returned |
| No GitHub repo yet → open-code-review can't run on PRs | Low | Build proceeds locally; PRs are opened as soon as the repo exists, and review happens then |

## Open questions

- None blocking tier 1. (Hosting and email provider are decided before the channel modules.)
