# Plan: Sentinel Foundations F3 — DB schema gap-fill (branch pointer)

**Status:** in-progress
**Created:** 2026-04-26
**Last updated:** 2026-04-26

## Goal

This branch (`feat/sentinel-foundations-f3-db-schema`) implements steps **F3.2**
and **F3.3** of the parent foundations plan. The branch-name plan-file hook
requires a plan at this path; this file is a thin pointer to the canonical
plan-of-record so status does not fragment.

## Scope

### In scope

- F3.2 — `AlertRequestRecord` SQLModel (RFC §12.3.1) + migration `008_alert_request_table.py`.
- F3.3 — `RunbookMatchRecord` SQLModel (RFC §12.3.2) + migration `009_runbook_match_table.py`.

### Out of scope

- All other F3 steps (F3.1 audit, F3.4–F3.11) — they live on the parent plan
  and ship on follow-up branches.

## Design Decisions

Recorded on the parent plan. Reference: see the F3.2 and F3.3 lines in
[`sentinel-hedgefund-foundations.md`](./sentinel-hedgefund-foundations.md).

| Decision | Choice | Why |
|----------|--------|-----|
| Plan-of-record location | parent plan, not this file | Avoids drift between two plan files for the same scope. |
| File location for SQLModels | `data/sql/alert_requests.py`, `data/sql/runbooks.py` | Mirrors the recent `data/` restructure (sql/ vs primitives/). |
| FK constraint name | `fk_runbook_match_alert_request` | Explicit name so downgrade can drop it deterministically. |

## Steps

- [x] Write failing unit tests for `AlertRequestRecord` and `RunbookMatchRecord`.
- [x] Implement both SQLModels in `data/sql/`.
- [x] Add migrations 008 and 009.
- [x] Wire both modules into `data/migrations/alembic/env.py`.
- [x] Verify migrations forward + reverse against a real Postgres.
- [ ] Run `just lint` and `just test` to close out.
- [ ] Two atomic commits.

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-26 | Recreated as a pointer plan after `just create-plan` | Branch hook requires the file; parent plan owns the design and status. |

## Outcome

_To be filled in once the work lands on `main` via PR._

### What was delivered

- `src/sentinel/data/sql/alert_requests.py` (`AlertRequestRecord`).
- `src/sentinel/data/sql/runbooks.py` (`RunbookMatchRecord`).
- `src/sentinel/data/migrations/alembic/versions/008_alert_request_table.py`.
- `src/sentinel/data/migrations/alembic/versions/009_runbook_match_table.py`.
- `tests/unit/data/sql/test_alert_requests.py`, `tests/unit/data/sql/test_runbooks.py`.
- `env.py` import block updated to register both models.

### Follow-up / tech debt

- Pre-existing autogen drift on `ticket_review_records.suggested_response`
  (model omits `nullable=False`, migration declares it). Out of scope for this
  branch — track separately if the parent plan picks it up.
