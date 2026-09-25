# Implementation Plan: verticals platform

Spec: `spec/SPEC-verticals.md`. Branch: `feat/verticals` off `feat/agent-core`.
Runs alongside the `evals` build (that work touches `evals/`, this touches `app/` and `verticals/`).

## Overview

Extract everything business-specific out of the code into `verticals/<id>/`, prove the abstraction
by standing up a second vertical (a homestay), then add three demo packs. The existing demo store
becomes `verticals/northwind` and must behave exactly as it does today — that's the regression test
for the whole extraction.

## Architecture decisions

- **Config is loaded once at startup** into a `VerticalConfig` (Pydantic), and passed down like
  `Settings` already is. No global lookups inside the agent loop.
- **Tools become a registry**, keyed by name; a vertical enables tools by name. Unknown name →
  startup error. `escalate` is always enabled and cannot be disabled.
- **Guardrail patterns come from config**, but the *mechanism* (check before the model, escalate,
  never answer) stays in code so a bad config can't loosen safety — worst case it fails to catch
  something, which the eval set then shows.
- **Prompts are templates** filled from `business:`. Prompt structure and the citation rule stay in
  code, because they're what makes the answers checkable.
- **`VERTICAL` env var selects the folder**, default `northwind`.
- YAML needs a parser: add `pyyaml` (new dependency; approving this plan approves it).

## Task list

### Phase 5: extraction
- [x] Task 18: `VerticalConfig` loader + `verticals/northwind/` (config + docs moved from `data/docs`)
- [x] Task 19: Prompts and escalation copy built from the business profile
- [x] Task 20: Tool registry; `get_order_status` / `get_product` registered and enabled by config
- [ ] Task 21: Guardrail patterns from config, with the refuse-outright category
- [ ] Task 22: Seed, ingest and evals take `--vertical`; KB and eval paths come from config

### Checkpoint E: northwind unchanged
- [ ] All existing tests pass with no edits to their assertions
- [ ] `git diff` on `app/agent/core.py` shows no behavioural change beyond reading config

### Phase 6: second vertical (proves the abstraction)
- [ ] Task 23: `check_availability` and `booking_enquiry` tools (enquiry → ticket, never a booking)
- [ ] Task 24: `verticals/seaside-homestay/` — profile, ~25 docs, 40-case eval set
- [ ] Task 25: Hospitality guardrails: no rate negotiation, no confirmed bookings, no promises about
      availability the tool didn't return

### Checkpoint F: two verticals
- [ ] Both verticals pass their own eval sets
- [ ] Switching `VERTICAL` changes behaviour with no code edits
- [ ] One conversation per vertical captured as a demo transcript

### Phase 7: demo packs
- [ ] Task 26: `appointment_request` + `lead_capture` tools
- [ ] Task 27: clinic pack (with the clinical-advice refusal), real-estate pack, coaching pack —
      ~15 docs and a 20-case eval set each

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Extraction quietly changes behaviour | High | northwind's existing tests are the contract; no test edits allowed in Phase 5 |
| Config sprawl — every new customer wants "one more setting" | Med | Only add a setting when a second vertical needs it; otherwise it stays in code |
| Guardrails weakened by a sloppy config | High | Mechanism stays in code; each vertical ships eval cases for its refusals |
| Clinic pack gives medical advice | High | Refuse-outright category, tested against docs that *do* contain the answer |
| Writing five knowledge packs is the real work | Med | Launch verticals get full packs; demo packs get 15 docs and are labelled as demos |

## Open questions

- Hospitality: is availability read from a config file (fine for a demo), or does it need a real
  channel manager / PMS integration to be sellable? Decide before Task 23.
