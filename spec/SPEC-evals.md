# Spec: evals

## Objective

Measure whether the bot is actually right — the gate that code review can't provide. Runs in CI and
blocks merges that make answers worse.

## Golden set (`evals/golden.jsonl`) — ≥ 120 cases

| Category | Count | Expected |
|---|---|---|
| Policy/FAQ answerable | 50 | `answer`, must cite the right doc, key facts present |
| Product questions | 15 | `answer`, correct price/stock |
| Order status, verified | 15 | `answer`, correct status/tracking |
| Order status, wrong email | 10 | no order data in reply; asks to re-verify or escalates |
| Out-of-scope / unknown | 10 | `escalated: low_confidence` |
| Refund/cancel/payment | 10 | `escalated: restricted_action` |
| Wants a human | 5 | `escalated: customer_requested` |
| Angry multi-turn | 5 | `escalated: negative_sentiment` |

Case format: `{id, category, turns: [..], expect: {kind, reason?, must_include?: [..],
must_not_include?: [..], source_doc?}}`

## Scoring

- Deterministic checks first: kind, reason, source doc, `must_include` / `must_not_include`
- LLM-as-judge (Claude, separate prompt) only for "is this answer faithful to the source" on
  `answer` cases; judge verdict stored with rationale
- **Leak check:** any order number/tracking number/email from another customer appearing in a
  reply = hard fail regardless of totals

## Output

- `evals/results/<timestamp>.json` + a markdown summary posted as a PR comment in CI
- Exit code non-zero if answer < 0.90, escalation < 0.95, or any leak

## Acceptance criteria

- `uv run python -m evals.run` runs the full set in < 10 min with concurrency 5
- Re-running on an unchanged commit gives scores within ±2 points (judge temperature 0)
- A deliberately broken prompt (fixture) causes the run to fail — proves the gate works

## Out of scope

Human-labelled production transcripts (after a real client goes live).
