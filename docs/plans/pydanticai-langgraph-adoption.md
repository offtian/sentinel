# Plan: PydanticAI + LangGraph Adoption

**Status:** draft
**Created:** 2026-04-26
**Last updated:** 2026-04-26

## Goal

Migrate Sentinel's orchestration layer from Pydantic Graph to LangGraph, harness-only — PydanticAI agents, the F2 envelope plumbing, vendor adapters, the data layer, and webhook routers all stay live. The driver is to settle the orchestration-framework decision (RFC §15.14, originally deferred to F5/month 3) by adopting LangGraph now and proving the patterns on the smallest pipeline before heavier work lands.

This umbrella plan covers all three pipeline migrations (support, SRE, chart) but only details the **support migration** (PR(N+1)). SRE and chart phases each get their own brainstorm + design + plan rounds when activated.

Full design rationale: [`docs/superpowers/specs/2026-04-26-pydanticai-langgraph-adoption-design.md`](../superpowers/specs/2026-04-26-pydanticai-langgraph-adoption-design.md). Step-level TDD detail: [`docs/superpowers/plans/2026-04-26-pydanticai-langgraph-adoption.md`](../superpowers/plans/2026-04-26-pydanticai-langgraph-adoption.md).

## Scope

### In scope (this plan, support-migration PR)

- New `src/sentinel/interfaces/workflows/` package; new support workflow built on LangGraph `StateGraph`
- `langgraph` + `langgraph-checkpoint-postgres` added to `pyproject.toml`
- `AsyncPostgresSaver` wired at app bootstrap; LangGraph's three checkpoint tables managed by `saver.setup()` (not Alembic)
- New `with_envelope` decorator in `interfaces/workflows/_envelope.py` (LangGraph counterpart to `_node_helpers.run_node_with_envelope`)
- Approval gate added to support pipeline (new behaviour — support did not have one before): `interrupt()` pauses workflow when confidence below threshold; resume via `Command(resume=...)`
- New approval endpoints for support: `POST /api/support/responses/{request_id}/approve`, `/reject`, `GET /approval-status` (mirror SRE shape)
- Existing webhook handler at `interfaces/api/routers/support/router.py` hard-cuts to call the new graph
- Existing `interfaces/graphs/support_review.py` moved to `interfaces/graphs/_archive/`
- Import-linter contracts forbid imports from `_archive/` and forbid cross-harness coupling
- ADR 0007 authored (`docs/adrs/0007-orchestration-framework-langgraph.md`); ADR 0006 closed as `accepted`
- Foundations plan amended: F5 collapses to LiteLLM-proxy-only; F6 + F8 retarget to `workflows/` SRE
- Tests rewritten on the new harness; archived-code tests deleted
- Documentation deltas: `docs/architecture.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`, `.env.default`

### Out of scope (deferred to later phases / plans)

- SRE migration to LangGraph — own plan after F3 (DB schema gap-fill) lands
- Chart-generation migration — absorbs F2 chart-generation envelope cleanup at the same time; own plan
- F3–F8 of the foundations plan — proceed independently per their existing schedule; F4–F8 land on `workflows/` SRE only when SRE has migrated
- LangGraph checkpoint cleanup / TTL job — tracked as tech debt; deferred to a follow-up plan
- Per-tenant or per-team workflow routing — single profile in foundations
- Shadow-mode / dual-running infrastructure — explicitly rejected in favour of W1 hard cutover for support

### Already shipped (no-op for this plan)

| Reference | Existing artefact |
|---|---|
| F2 Envelope identity | `data/primitives/envelope.py`, `interfaces/api/middleware.py` (PR #23) |
| F2 envelope-bound node helpers | `interfaces/graphs/_node_helpers.py` (continues to serve legacy SRE/chart graphs) |
| PydanticAI agent factories | `interfaces/graphs/agents/{ticket_reviewer,response_drafter}.py` (reused by new workflow) |
| Confidence scoring primitives | `domain/confidence/entities.py` (`ConfidenceScore.from_factors`) |

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Adoption strategy | Greenfield rebuild, **boundary 1** — orchestration glue only archives | Preserves F2 envelope work, PydanticAI agent factories, and `_node_helpers` primitives that were intentionally framework-agnostic |
| Location | `interfaces/workflows/` for new code; `interfaces/graphs/_archive/` for archived legacy | "Workflows" reads as a deliberate framework choice; `_archive/` + import-linter contract mechanically prevents accidental backsliding |
| Migration order | Support → SRE → Chart | Support is the smallest, lowest-blast-radius pipeline; F3 (DB schema) lands between support and SRE so SRE inherits canonical schema |
| State shape | TypedDict at LangGraph boundary; `Envelope`, `ConfidenceScore` etc. keep existing types inside | Matches LangGraph's idiomatic state pattern without re-typing domain primitives |
| LangGraph idioms | `AsyncPostgresSaver` + `interrupt()` from support migration onward | Pays integration cost once, on smallest pipeline; SRE inherits a settled pattern |
| Foundations interleave | Support → F3 → SRE → F4–F8 on `workflows/` only | F3 is data-layer-only; F4–F8 add nodes written once on LangGraph |
| Webhook cutover | Hard cutover (W1) for support; SRE will use feature flag (W2) when its design lands | Support is shadowed by human review; SRE auto-investigates production alerts and earns the safety belt |
| Schema ownership | LangGraph's three tables managed by `saver.setup()`, not Alembic | Library upgrades add columns; Alembic-tracked schema would fight upgrades |
| Persistence stores | Three coexist: app schema (audit) / checkpointer (resume state) / replay bundle (deterministic re-execution) | Each store has a different purpose |
| ADR ownership | ADR 0007 authored in this PR (not deferred to F5) | Decision is being made now; F5 collapses to LiteLLM-proxy-only |
| Test strategy for archived code | Delete archived-code tests; do not move to `_archive/` | Archived code is reference material only; CI signal lives where new tests run |
| New approval gate on support | Adding it as new behaviour | CI1 commits to LangGraph-native idioms; support gains parity with SRE's approval surface and proves the `interrupt()`/`Command(resume=...)` round-trip |

## Architecture

### Phasing

```
PR(N+1) — Support migration to LangGraph (this plan's implementation scope)
PR(N+2) — F3 DB schema gap-fill (independent; data layer only)
PR(N+3+) — SRE migration to LangGraph (own design + plan round)
PR(N+4+) — F4 / F6 / F7 / F8 land on workflows/ SRE only
PR(final) — Chart workflow migration + envelope cleanup (own design + plan round)
```

### Directory layout (after support-migration PR)

```
src/sentinel/interfaces/
├── graphs/                                # legacy harness, frozen
│   ├── _archive/
│   │   ├── __init__.py                    # marker; no re-exports
│   │   └── support_review.py              # MOVED — no longer imported from outside _archive
│   ├── sre_investigation.py               # untouched (still serving traffic)
│   ├── chart_generation.py                # untouched
│   ├── agents/                            # PydanticAI agent factories — STAY HERE
│   │   ├── ticket_reviewer.py             # used by both legacy SRE and new workflows/
│   │   └── response_drafter.py
│   └── _node_helpers.py                   # untouched — legacy graphs keep using it
└── workflows/                             # NEW
    ├── __init__.py
    ├── _envelope.py                       # NEW — with_envelope decorator
    ├── _checkpointer.py                   # NEW — AsyncPostgresSaver builder
    ├── support_review.py                  # NEW — LangGraph StateGraph + node functions
    └── support_state.py                   # NEW — SupportReviewState TypedDict
```

### Persistence model

After this PR, a `request_id` has rows in three stores simultaneously:

| Store | Purpose | Source of truth for |
|---|---|---|
| App schema (`response_suggestion`, `confidence_score`, etc.) | Canonical audit | Reporting, UI, billing |
| LangGraph checkpointer (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) | Runtime resume state | "Where did this workflow pause?" |
| Replay bundle (PR #15, extended in F4) | Deterministic re-execution | Bit-for-bit replay |

Three concerns, three stores. None subsumes the others.

## Steps

The detailed TDD step list lives in [`docs/superpowers/plans/2026-04-26-pydanticai-langgraph-adoption.md`](../superpowers/plans/2026-04-26-pydanticai-langgraph-adoption.md). High-level checkboxes mirror that plan's task list:

- [ ] **T1** Spike — verify `AsyncPostgresSaver` + `interrupt()` + `Command(resume=...)` round-trip in a scratch integration test
- [ ] **T2** Add deps (`langgraph`, `langgraph-checkpoint-postgres`) to `pyproject.toml`; `uv lock`
- [ ] **T3** Settings additions: `langgraph_checkpoint_dsn` field on `Settings`; `.env.default` entry
- [ ] **T4** Scaffold `src/sentinel/interfaces/workflows/` package (`__init__.py` only)
- [ ] **T5** `_envelope.py` — `with_envelope` decorator (TDD)
- [ ] **T6** `_checkpointer.py` — `build_checkpointer()` (TDD)
- [ ] **T7** `support_state.py` — `SupportReviewState` TypedDict
- [ ] **T8** Port `classify_ticket` node as async function (TDD)
- [ ] **T9** Port `search_documentation` node as async function (TDD)
- [ ] **T10** Port `draft_response` node as async function (TDD)
- [ ] **T11** Port `determine_confidence` node — adds `needs_approval` flag (TDD)
- [ ] **T12** New `wait_for_human` node using `interrupt()` (TDD)
- [ ] **T13** `_route_after_confidence` conditional edge (TDD)
- [ ] **T14** `build_support_review_graph()` — composes the StateGraph (TDD)
- [ ] **T15** App lifespan: build checkpointer + compiled graph; expose on `app.state`
- [ ] **T16** New `review_ticket(...)` entrypoint in `workflows/support_review.py` that wraps `graph.ainvoke` (TDD)
- [ ] **T17** Update Jira webhook handler in `interfaces/api/routers/support/router.py` to call the new entrypoint
- [ ] **T18** New approval endpoints: `POST /approve`, `POST /reject`, `GET /approval-status` under `interfaces/api/routers/support/`
- [ ] **T19** Functional E2E test: webhook → high-confidence path → END
- [ ] **T20** Functional E2E test: webhook → low-confidence interrupt → approve endpoint → resume → END
- [ ] **T21** Move `interfaces/graphs/support_review.py` to `interfaces/graphs/_archive/support_review.py`
- [ ] **T22** Delete archived-code tests under `tests/integration/interfaces/graphs/test_support_*.py` and `tests/functional/test_support_review.py`
- [ ] **T23** Add import-linter contracts: forbid imports from `_archive/`; forbid `workflows` importing from legacy active sibling DAGs
- [ ] **T24** Update non-graph callers of legacy `review_ticket`: `worker.py`, `replay.py`, `interfaces/chat/app.py`, `interfaces/slack/event_handlers.py` — point at the new `workflows.support_review.review_ticket`
- [ ] **T25** Author ADR 0007 (`docs/adrs/0007-orchestration-framework-langgraph.md`)
- [ ] **T26** Close ADR 0006 — fill in `Decision`, `Consequences`, `Validation` sections; status → `accepted`
- [ ] **T27** Update foundations plan (`docs/plans/sentinel-hedgefund-foundations.md`) per the amendment list
- [ ] **T28** Update `docs/plans/INDEX.md`
- [ ] **T29** Update `docs/architecture.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`
- [ ] **T30** Final QA: `just lint-fix`, `just lint`, `just test`, `just test-integration`; ensure CI green; commit + open PR

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-26 | Initial draft | Brainstorm decisions ratified per `docs/superpowers/specs/2026-04-26-pydanticai-langgraph-adoption-design.md` |

## Outcome

_Fill in after completion._

### What was delivered

- ...

### Follow-up / tech debt

- LangGraph checkpoint cleanup job (TTL on completed threads) — captured in spec; needs own follow-up plan
- SRE migration plan — kicks off after F3 (DB schema) ships
- Chart migration plan — absorbs F2 chart-generation envelope cleanup
- Connection pool tuning for `AsyncPostgresSaver` — separate pool from the app's SQLAlchemy pool today; revisit under load
