# Support Agent — instructions for coding agents

Read `SPEC.md` first, then the module spec for the task at hand (`spec/SPEC-<module-id>.md`).
The capability map in `SPEC.md` is the index of modules and build order.

## Workflow (agent-skills plugin)

- New module → `/spec` (module spec) → `/planning` → `/build auto` (one human approval per module)
- Before opening a PR → `/review`, then `/code-simplify` if the diff is > 300 lines
- `/build auto` must stop and ask for: new dependencies, schema changes after `orders` ships,
  CI changes, anything touching secrets or real external accounts

## Code intelligence (codebase-memory-mcp)

- Session start: `index_repository` on this repo if `index_status` says stale
- Before editing a function you haven't read this session: `trace_path` (inbound) to see callers
- Before committing: `detect_changes` to list impacted symbols; add tests for any impacted public function
- After a module ships: `manage_adr` to record the key design decision

## Review (open-code-review)

- Every PR is reviewed automatically by the `OpenCodeReview PR Review` workflow
- Blocking comments (security/correctness) must be fixed or answered before merge
- Locally: `ocr review --from main` before pushing

## Hard rules

- Never commit `.env` or any key. Never add write methods to `app/orders/`.
- Never skip, delete, or loosen a test or eval threshold without explicit human approval.
- Commands, structure and style: see `SPEC.md`.
