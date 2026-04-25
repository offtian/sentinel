# Plan: Sentinel Foundations — Phase F0 Validation Sprint (ADR Drafts)

**Status:** in-progress
**Created:** 2026-04-25
**Last updated:** 2026-04-25

## Goal

Stand up the ADR scaffolding for Phase F0 of the foundations plan: a canonical
ADR template plus six draft skeletons (D-11, D-12, D-13, D-15, D-16, O-10) that
get carried into stakeholder conversations during the F0 validation sprint and
filled in with outcomes.

This branch ships **drafting only** — no production code, no tests. The ADR
template establishes the format every subsequent Sentinel ADR will follow; the
six skeletons are pre-populated with context, options, and fallbacks so the
named owner only has to confirm decision + consequences in the meeting.

Parent plan: [`sentinel-hedgefund-foundations.md`](sentinel-hedgefund-foundations.md)
(specifically the F0 phase steps F0.1–F0.8).

## Scope

### In scope

- New directory `docs/adrs/` with one canonical template.
- Six draft ADR skeletons covering D-11, D-12, D-13, D-15, D-16, O-10 with
  Context / Options / Fallback / References fully populated from RFC §11.1 and
  §11.4; Decision / Consequences / Validation as labelled placeholders.
- Cross-links between sibling ADRs (e.g. 0003 → 0004 / 0005 for the per-service
  slices of D-13; 0006 → forward-reference to the F5 ADR 0007 on orchestration
  framework choice).

### Out of scope

- Filling in the Decision / Consequences / Validation sections — that happens
  during the stakeholder conversations in Day 1–5 of the sprint.
- Updating `docs/plans/INDEX.md` (parent foundations plan tracks F0 status).
- Any code, tests, or migrations. F1 work is gated on F0 closing per the
  foundations-plan acceptance criteria.

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| ADR format | Michael-Nygard-style with YAML frontmatter | Frontmatter gives machine-parseable status / owner / RFC refs; sectioned body keeps the conversation outcome legible. |
| File naming | `NNNN-<short-id>-<slug>.md` mirroring RFC `D-*` / `O-*` numbers | Traceability — auditors / future engineers can map ADR back to RFC decision row without a lookup table. |
| Skeletons pre-populated | Yes (Context / Options / Fallback / References) | Saves the named owner's time in the meeting; they confirm or amend rather than co-author the document live. |

## Steps

- [x] Step 1: Create `docs/adrs/_template.md` with frontmatter spec, section
  order, and naming-convention comment block.
- [x] Step 2: Create `0001-D11-on-prem-only.md` skeleton.
- [x] Step 3: Create `0002-D12-monorepo.md` skeleton.
- [x] Step 4: Create `0003-D13-firm-shared-infra.md` skeleton (umbrella;
  cross-links to 0004 + 0005).
- [x] Step 5: Create `0004-D15-langfuse-rbac.md` skeleton.
- [x] Step 6: Create `0005-D16-postgres-pgvector.md` skeleton (with note that
  case-history is out of foundations scope).
- [x] Step 7: Create `0006-O10-pydanticai-langgraph.md` skeleton (forward-links
  to ADR 0007 for the F5 orchestration-framework decision).

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-25 | Initial draft of the seven files. | F0.1 kicks off the validation sprint; ADRs need to exist before Day 1. |

## Outcome

_Fill in after completion._

### What was delivered

- ...

### Follow-up / tech debt

- The Decision / Consequences / Validation sections in `0001`–`0006` will be
  filled in by the named owners during Day 1–5 of the F0 sprint, then merged
  via separate PRs (one per ADR or one per day, controller's choice).
- ADR 0007 (orchestration framework: Pydantic Graph vs LangGraph) is deferred
  to phase F5 of the foundations plan and is referenced from ADR 0006.
