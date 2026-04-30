# Plan: LangGraph SRE Migration — Phase 7 (Cutover + Cleanup)

**Status:** in-progress
**Created:** 2026-04-30
**Last updated:** 2026-04-30

## Goal

Execute the post-soak cutover and cleanup tasks (T46–T58) from the
`langgraph-sre-migration` plan. T44 (staging soak) and T45 (prod cutover)
are operational steps that cannot be automated; this plan covers the code
and documentation cleanup that follows them.

## Scope

### In scope

- T46: Move `interfaces/graphs/investigation.py` to `interfaces/graphs/_archive/`
- T47: Delete legacy SRE unit tests under `tests/unit/interfaces/graphs/test_investigation*.py`
- T48: Update import-linter contracts — forbid `_archive` imports, ensure workflow imports permitted
- T49: SKIPPED — requires staging soak completion; flag branch intentionally preserved in worker.py
- T50: SKIPPED — same operational gate as T49
- T51: Update `docs/architecture.md` §Pipelines to describe LangGraph SRE
- T52: Update `CLAUDE.md` with `interfaces/workflows/` canonical home note
- T53: Update `AGENTS.md` with `interfaces/workflows/` import pattern example
- T54: Update `README.md` pipeline table and env-var table for LangGraph SRE
- T55: Mark `langgraph-sre-migration` complete in `docs/plans/INDEX.md`
- T56: Tick PRD criterion for SRE on LangGraph
- T57: Audit `sentinel-hedgefund-foundations.md` for SRE-on-pydantic-graph references
- T58: Final validation — `just lint` + `just test` both green

### Out of scope

- T44 / T45: operational staging and production soak steps (cannot be scripted)
- Removing the `langgraph_sre_enabled` flag branch from `worker.py` (T49 skip)
- Removing legacy approval-flow branches from API/Slack (T50 skip)
- Chart-coding pipeline migration (separate plan)

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| T49/T50 skip | Leave flag branch in worker + endpoints | Staging soak not complete; rollback path must remain intact until confirmed |
| Archive via file move | Move file, update parity test import | Preserves git history; parity test still needs legacy for comparison |
| Import-linter | Forbidden contract on `_archive` | Ensures no new code accidentally depends on archived implementation |

## Steps

- [ ] T46: Archive legacy investigation pipeline
- [ ] T47: Delete legacy unit tests
- [ ] T48: Update import-linter contracts
- [ ] T51: Update `docs/architecture.md` §AI SRE Pipeline
- [ ] T52: Update `CLAUDE.md` Architecture (non-obvious)
- [ ] T53: Update `AGENTS.md` import pattern example
- [ ] T54: Update `README.md` pipeline description + env-var table
- [ ] T55: Mark `langgraph-sre-migration` complete in `docs/plans/INDEX.md`
- [ ] T56: Tick PRD criterion for SRE on LangGraph
- [ ] T57: Audit `sentinel-hedgefund-foundations.md`
- [ ] T58: Final validation

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-30 | Created plan | Phase 7 implementation begins |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- Remove `langgraph_sre_enabled` flag and legacy paths once staging soak confirms stability (T49/T50)
- Chart-coding pipeline migration — own plan
