---
id: "0004"
title: "Per-team Langfuse projects with RBAC + tag-based filtering (D-15)"
status: proposed
date: 2026-04-25
decision_owner: "Langfuse operator"
reviewers: []
rfc_refs:
  - "§11.1"
  - "§11.4"
  - "§7.2"
  - "§13.6"
supersedes: null
superseded_by: null
---

# ADR 0004 — Per-team Langfuse projects with RBAC + tag-based filtering (D-15)

## Context

Validate RFC decision **D-15**: Sentinel uses the firm's existing Langfuse
instance, with one project per Platform Engineering team profile (`sentinel-sre`,
`sentinel-devops`, `sentinel-ace`, `sentinel-platform`), enforcing PM
information-barrier via project-level RBAC and trace-tag filtering on
`tenant_id`.

This is a *tentative* decision in the RFC ([§11.1 D-15](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning))
that depends on which Langfuse version the firm runs and what RBAC features it
ships. Day-4 conversation with the Langfuse operator pins it down.

**Working assumption.** Firm runs Langfuse v3+ (or equivalent) with
project-level RBAC, and that scoping access by trace tag (`tenant_id`) via
API filters is sufficient for the per-PM information barrier described in
[§7.2](../../Sentinel/RFC-001-sentinel-hedgefund.md#72-langfuse-projects-one-per-platform-team-not-per-pm).
The redactor runs *before* trace export ([§13.6](../../Sentinel/RFC-001-sentinel-hedgefund.md#136-otel-collector-configuration-the-redaction-layer)),
so what reaches Langfuse is already safe-to-read across PMs by anyone with
team-project access; per-PM scoping reconstructs via tag filters.

**What hangs on this.** Phase F4 (observability wiring) sets up per-team OTEL
exporters routing on `sentinel.team_profile` to the right Langfuse project.
The choice between "shared Langfuse + RBAC" and "Langfuse-instance-per-team"
changes which exporter endpoints F4 wires up and how many sets of API keys
have to live in the firm's secret store.

**Inputs to bring** (per [§11.4 "Things to bring"](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)):
the §7.2 per-team-project shape, the §13.6 OTEL collector routing config, and
the open question on trace-tag-RBAC strength tracked as O-09 in §11.2.

## Options considered

- **A. Shared Langfuse, per-team projects + project-RBAC + tag-based filtering.**
  One Langfuse instance, four projects (`sentinel-sre`, `sentinel-devops`,
  `sentinel-ace`, `sentinel-platform`). Engineers see traces tagged with
  their assigned PM scope; "operator" role bypasses tag filter for the
  duration of an investigation, audited. Tradeoffs: low ops cost; relies on
  RBAC features being strong enough that O-09 is answered yes.
- **B. One Langfuse per team-profile (3 instances).** Recovers the operator
  boundary at the instance level rather than the project level. Tradeoffs:
  3× the ops cost, three sets of credentials, three upgrade paths to track;
  but no reliance on tag-based RBAC strength.
- **C. Shared Langfuse, no RBAC, tag-based filtering only.** Single project
  per team, every engineer sees everything in their team's project, PM
  scope enforced only at view time via UI tag filter. Tradeoffs: simplest
  ops; weakest information barrier; defensible only because the redactor
  runs at write-time so MNPI never lands in Langfuse anyway.

## Decision

_To be filled in after the Day-4 validation conversation with the Langfuse
operator._

## Consequences

_To be filled in after the Day-4 validation conversation._

## Fallback if reversed

If Langfuse RBAC is too weak to cleanly support tag-based filtering on
`tenant_id` (option A fails the operator's bar), fall back to **option B**:
run a Langfuse instance per team-profile (3 instances). Cost: more ops than
option A; recovers the tenant boundary at the instance level rather than the
project level. RFC §11.4 captures this as the named fallback.

If even option B is too costly to operate and the firm prefers C
(no-RBAC + tag-only filtering), this becomes a compliance ask rather than a
foundations call — defer to month 3 and document via amendment. The redactor
running pre-export ([§13.6](../../Sentinel/RFC-001-sentinel-hedgefund.md#136-otel-collector-configuration-the-redaction-layer))
is what makes option C technically defensible.

## Validation

_To be filled in after the Day-4 validation conversation._

Expected artefacts to capture: Langfuse version string; the actual RBAC
features available (project-level, tag-level, per-user); a test project
called `sentinel-sre` created during the conversation with trace-tag-based
RBAC attempted; per-team API keys (kept in firm KMS, not in the repo);
named owner sign-off in `reviewers` frontmatter.

## References

- RFC §11.1 D-15: [Decisions made (with reasoning)](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)
- RFC §11.4: [First-month validation plan for tentative decisions](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)
- RFC §7.2: [Langfuse projects: one per platform team, not per PM](../../Sentinel/RFC-001-sentinel-hedgefund.md#72-langfuse-projects-one-per-platform-team-not-per-pm)
- RFC §13.6: [OTEL collector configuration (the redaction layer)](../../Sentinel/RFC-001-sentinel-hedgefund.md#136-otel-collector-configuration-the-redaction-layer)
- RFC §11.2 O-09: open question on Langfuse tag-based RBAC strength
- Foundations plan: [`docs/plans/sentinel-hedgefund-foundations.md`](../plans/sentinel-hedgefund-foundations.md), Phase F0.6
- Sibling ADRs: [0003 D-13 firm-shared infra](./0003-D13-firm-shared-infra.md) (Langfuse is one of the four umbrella services)
