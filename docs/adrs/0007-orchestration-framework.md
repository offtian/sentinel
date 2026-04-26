---
id: "0007"
title: "Orchestration framework: Pydantic Graph vs LangGraph (F5)"
status: accepted
date: 2026-04-26
decision_owner: "Sentinel engineering"
reviewers: []
rfc_refs:
  - "§2.3"
  - "§3.8"
  - "§15.14"
supersedes: null
superseded_by: null
---

# ADR 0007 — Orchestration framework: Pydantic Graph vs LangGraph (F5)

## Context

Phase F5 of the foundations plan asks for a written decision on the
orchestration framework — Pydantic Graph (current) vs LangGraph (proposed in
RFC v0.4 §2.3). [ADR 0006](./0006-O10-pydanticai-langgraph.md) deliberately
deferred this call so the *agent framework* choice (PydanticAI) and the
*orchestration framework* choice could be decided independently.

Two factors converge on this decision:

1. **Replay determinism** is the single foundations failure mode (RFC §14.7).
   F4 Phase B (PR #29) shipped a bundle-based replay path on Pydantic Graph
   — RFC §3.8 ReplayBundle persistence, recorded transports, 30-run
   determinism CI — proven against the existing SRE pipeline. LangGraph's
   selling point here is its native checkpoint mechanism, but checkpoints
   solve a problem the bundle path now solves.
2. **Foundations schedule.** F5 sits in week 2. F6 (runbook catalog), F7
   (capability tokens), and F8 (groundedness gate) all touch pipeline nodes.
   A framework swap mid-foundations would block F6/F7/F8 by reshaping the
   nodes they're modifying.

A separate **migration plan** for LangGraph adoption already exists
(`docs/plans/pydanticai-langgraph-adoption.md`, in-progress) — it sequences
support-pipeline migration first, then SRE/chart-coding under their own
plans. That plan is the right vehicle for the swap; F5 is the right place to
record that the swap is *not* happening inside foundations.

## Options considered

- **A. Stay on Pydantic Graph for foundations; revisit at month 3.** Keep
  current pipelines unchanged. Bundle-based replay (PR #29) is the
  foundations replay mechanism. LangGraph adoption proceeds via the existing
  migration plan, support-pipeline first, after F8 closes. Tradeoffs: zero
  framework-swap risk during F5–F8; loses LangGraph's checkpoint replay (but
  the bundle path covers the requirement); LangGraph migration becomes a
  follow-on initiative with its own test gates.
- **B. Migrate to LangGraph during F5.** Rewrite SRE + support pipelines on
  LangGraph in week 2. Tradeoffs: ~5 days framework-swap cost (RFC §11.4);
  blocks F6/F7/F8 until the rewrite stabilises; no replay-determinism
  benefit beyond what the bundle path already delivers; rebases the in-flight
  support-pipeline migration plan from "incremental" to "foundations-blocker".
- **C. Big-bang both pipelines later, no F5 decision.** Defer the decision
  past F5. Tradeoffs: leaves ADR 0007 as a perpetual TBD; adoption plan stays
  unanchored; F4 phase-B replay cost (already paid) is then partially
  redundant if LangGraph checkpoints replace it.

## Decision

Adopt **option A**: stay on Pydantic Graph for foundations (F5–F8). Revisit
at month 3 via the existing `pydanticai-langgraph-adoption.md` plan, with
support-pipeline migration as the first slice and SRE/chart-coding migrations
following under dedicated sub-plans.

## Consequences

- F5 ships with **no orchestration framework swap**. F5's remaining work
  (F5.2–F5.7) is purely the LiteLLM proxy migration.
- F4 Phase B's bundle-based replay (PR #29) is the canonical replay
  mechanism through F8. The 30-run determinism CI gates regressions.
- F6 (runbook catalog), F7 (capability tokens), F8 (groundedness gate) land
  on Pydantic Graph nodes — no rebase required when LangGraph migration runs.
- The LangGraph migration plan (`docs/plans/pydanticai-langgraph-adoption.md`,
  in-progress) keeps its current sequencing: support pipeline first, then
  SRE and chart-coding under their own plans.
- ADR 0006 — the forward link "ADR 0007 (forthcoming)" — is now resolved.
  ADR 0006's option C (PydanticAI + Pydantic Graph, defer LangGraph) is the
  effective foundations posture.

## Fallback if reversed

If month-3 review flips this decision (e.g., bundle-based replay surfaces a
gap LangGraph checkpoints would close), the migration cost is ~5 days per
pipeline (RFC §11.4 D-01 row). The migration plan already scopes this; no
foundations rewrite needed because all F5–F8 work is at the
node/agent/runbook layer, not the graph runtime layer.

If the bundle-based replay regresses on the 30-run determinism CI between
now and month 3, escalate immediately rather than waiting for the scheduled
review — that signal is what would justify earlier LangGraph adoption.

## Validation

- F4 Phase B 30-run determinism CI passing on `main` (PR #29 merged
  2026-04-26) — see `tests/integration/test_replay_determinism.py`.
- LangGraph adoption plan exists with named phases:
  `docs/plans/pydanticai-langgraph-adoption.md`.
- ADR 0006 forward reference to this ADR resolved.

## References

- RFC: [`Sentinel/RFC-001-sentinel-hedgefund.md`](../../Sentinel/RFC-001-sentinel-hedgefund.md)
  - §2.3 Agent framework
  - §3.8 ReplayBundle
  - §15.14 Agent framework re-evaluation
- Foundations plan: [`docs/plans/sentinel-hedgefund-foundations.md`](../plans/sentinel-hedgefund-foundations.md), Phase F5
- LangGraph adoption plan: [`docs/plans/pydanticai-langgraph-adoption.md`](../plans/pydanticai-langgraph-adoption.md)
- Sibling ADR: [`./0006-O10-pydanticai-langgraph.md`](./0006-O10-pydanticai-langgraph.md) — agent framework decision (defers orchestration to this ADR)
