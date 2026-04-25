---
id: "0003"
title: "Reuse firm-shared LiteLLM / OTEL / Langfuse / Postgres (D-13)"
status: proposed
date: 2026-04-25
decision_owner: "LiteLLM operator (platform-platform team)"
reviewers: []
rfc_refs:
  - "§11.1"
  - "§11.4"
  - "§2.4"
  - "§13"
supersedes: null
superseded_by: null
---

# ADR 0003 — Reuse firm-shared LiteLLM / OTEL / Langfuse / Postgres (D-13)

## Context

Validate RFC decision **D-13**: instead of standing up Sentinel-dedicated
infrastructure, reuse four already-deployed firm services — the LiteLLM proxy,
the OTEL collector, Langfuse, and the shared Postgres cluster.

This is a *tentative* decision in the RFC ([§11.1 D-13](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning))
pending Day-3 to Day-5 conversations with the operator of each service. It is
the single most consequential tentative decision in the foundations plan: if
all four services are available as advertised, ~3 weeks of infra build
collapses into integration work, and §14 of the RFC is rebuilt around that
collapse.

**This ADR captures the umbrella decision and the LiteLLM-specific slice.**
The Langfuse and Postgres slices are co-validated with their dedicated owners
on Day 4 and tracked in their own ADRs:

- Langfuse RBAC / per-team projects → [ADR 0004](./0004-D15-langfuse-rbac.md) (D-15)
- Postgres + pgvector + per-DB role separation → [ADR 0005](./0005-D16-postgres-pgvector.md) (D-16)

The umbrella decision recorded here is "reuse all four"; the per-service ADRs
record the operator-specific conditions and any per-service flips.

**Working assumption.** All four services are available, with shapes that
match the §13 contract (per-tenant tags on every span, OTLP export to Langfuse,
shared Postgres with pgvector + per-database role separation). The
LiteLLM-specific request, validated on Day 3, is for a virtual key carrying
`tenant_id`, `team_profile`, and `pii_class` tags as headers, with OTLP routing
to firm Langfuse and a list of tool-use-validated on-prem models per
[§2.4](../../Sentinel/RFC-001-sentinel-hedgefund.md#24-litellm-proxy-as-the-llm-chokepoint--on-prem-only-d-11).

**Inputs to bring** (per [§11.4 "Things to bring"](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)):
the §2.4 + §13 contracts for tenant tagging, the list of on-prem candidate
models from ADR 0001 (D-11), and the BFCL + custom Sentinel tool-use eval
expectations.

## Options considered

- **A. Reuse all four (LiteLLM, OTEL, Langfuse, Postgres).** Tradeoffs: lowest
  build cost; integration discovery risk in week 1; Sentinel adopts each
  service's existing operational conventions.
- **B. Reuse a subset, build the rest.** Most likely if Langfuse RBAC or
  Postgres `pgvector` extension fall short. Tradeoffs: per-service cost of
  ~3–5 days, scoped where the firm gap lives.
- **C. Stand up Sentinel-dedicated minimal versions of all four.** A single
  Helm chart can spin up Langfuse + Postgres; OTEL collector + LiteLLM are
  each their own Helm chart. Tradeoffs: full control, no operator dependency,
  but ~3 weeks of infra work re-introduced — exactly the work D-13 is meant to
  collapse.

## Decision

_To be filled in after the Day-3 (LiteLLM) conversation, with cross-references
into ADRs 0004 and 0005 for the Day-4 conclusions._

## Consequences

_To be filled in after the Day-3 conversation. Expect this section to grow as
ADRs 0004 and 0005 close — track per-service follow-ups here as bullet points
with links into those ADRs._

## Fallback if reversed

If any service is unavailable: stand up a Sentinel-dedicated minimal version
via Helm chart. Cost per RFC §11.4: **~3–5 days per missing service**.
Worst-case (all four unavailable) reverts to the original §14 plan — **~3
weeks** of infra work re-introduced, but with the saving that we know in
week 1, not week 4.

Per-service flip plans:

- **LiteLLM unavailable.** Self-host LiteLLM proxy in a Sentinel-owned cluster
  alongside the on-prem vLLM backends. Most expensive of the four because the
  proxy carries the §2.4 routing rules.
- **OTEL collector unavailable.** Run a Sentinel-local collector that exports
  to whichever Langfuse instance is in scope. Cheap (~1 day).
- **Langfuse unavailable.** See [ADR 0004](./0004-D15-langfuse-rbac.md) for
  the per-team-instance fallback.
- **Postgres unavailable.** See [ADR 0005](./0005-D16-postgres-pgvector.md)
  for the dedicated-RDS fallback.

## Validation

_To be filled in after the Day-3 to Day-5 conversations._

Expected artefacts to capture: a working LiteLLM virtual key with the three
required tags (kept in 1Password / CI secret store, **not** in the repo);
OTLP collector endpoint and required attribute list; per-team Langfuse project
names; Postgres database creation request with pgvector extension; named
owner sign-off per service.

## References

- RFC §11.1 D-13: [Decisions made (with reasoning)](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)
- RFC §11.4: [First-month validation plan for tentative decisions](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)
- RFC §2.4: [LiteLLM proxy as the LLM chokepoint — on-prem only (D-11)](../../Sentinel/RFC-001-sentinel-hedgefund.md#24-litellm-proxy-as-the-llm-chokepoint--on-prem-only-d-11)
- RFC §13: [OTEL pipeline + Langfuse integration](../../Sentinel/RFC-001-sentinel-hedgefund.md#13-otel-pipeline--langfuse-integration)
- Foundations plan: [`docs/plans/sentinel-hedgefund-foundations.md`](../plans/sentinel-hedgefund-foundations.md), Phase F0.5
- Sibling ADRs:
  - [0001 D-11 on-prem only](./0001-D11-on-prem-only.md) (model allowlist for the LiteLLM virtual key)
  - [0004 D-15 Langfuse RBAC](./0004-D15-langfuse-rbac.md) (Day-4 Langfuse slice)
  - [0005 D-16 Postgres + pgvector](./0005-D16-postgres-pgvector.md) (Day-4 DBA slice)
