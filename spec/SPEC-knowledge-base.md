# Spec: knowledge-base

## Objective

Make the store's policy/FAQ/catalog content searchable so `agent-core` can ground every answer in a
cited source, and can tell when nothing relevant exists (the main escalation signal).

## Inputs

- `data/docs/*.md` — ~30 markdown docs (shipping, returns, warranty, payments, sizing, care, contact…).
  Front-matter: `title`, `category`, `updated`.
- Product catalog rows from `orders` seed data (name, sku, description, price, stock) — indexed as
  one chunk per product.

## Behaviour

- `ingest()` — idempotent. Splits docs by heading (max ~400 tokens, 50-token overlap), embeds with
  fastembed, stores chunks + vectors in SQLite. Re-running with unchanged content changes nothing
  (content hash per chunk).
- `search(query: str, k: int = 5) -> list[Hit]` — hybrid: cosine similarity + BM25, merged with
  reciprocal-rank fusion. `Hit` = `chunk_id, doc_title, text, score (0–1), source_path`.
- `best_score` from a search is exposed so `agent-core` can apply a relevance threshold.

## Interface (consumed by agent-core)

```python
class KnowledgeBase(Protocol):
    def search(self, query: str, k: int = 5) -> list[Hit]: ...
```

## Acceptance criteria

- Seeding produces ≥ 30 docs and 50 product chunks
- For 20 hand-written queries in `tests/knowledge_base/test_search_quality.py`, the correct doc is in
  top-3 for ≥ 18
- An off-topic query ("what's the weather in Paris") returns best score below the configured threshold
- Re-ingest with no changes: 0 rows written (asserted)
- Search over the demo corpus < 50 ms p95 locally

## Out of scope

Admin UI for editing docs (edit the markdown files); multilingual search.
