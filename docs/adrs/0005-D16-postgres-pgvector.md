---
id: "0005"
title: "Firm shared Postgres for sentinel_app + sentinel_audit, pgvector for case-history (D-16)"
status: proposed
date: 2026-04-25
decision_owner: "DBA / Database Team"
reviewers: []
rfc_refs:
  - "§11.1"
  - "§11.4"
  - "§12.3"
  - "§3.3.1"
supersedes: null
superseded_by: null
---

# ADR 0005 — Firm shared Postgres for sentinel_app + sentinel_audit, pgvector for case-history (D-16)

## Context

Validate RFC decision **D-16**: Sentinel uses the firm's shared Postgres
cluster, requesting two databases — `sentinel_app` (operational pipeline
state) and `sentinel_audit` (the WORM-style append-only audit log) — split for
the role separation in
[§12.3.10](../../Sentinel/RFC-001-sentinel-hedgefund.md#1230-audit_log--append-only-worm-style)
and using the `pgvector` extension for case-history retrieval.

This is a *tentative* decision in the RFC ([§11.1 D-16](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning))
pending the Day-4 conversation with the DBA / database team. Two firm-side
gaps would force a flip: a shared cluster that cannot host `pgvector` (some
managed Postgres versions cannot), or one that does not support per-database
role separation.

**Working assumption.** Shared Postgres cluster supports both `pgvector` and
per-database role separation (so `audit_writer` is the only role that can
INSERT into `sentinel_audit.audit_log`, and other roles get SELECT only per
[§12.3.10](../../Sentinel/RFC-001-sentinel-hedgefund.md#1230-audit_log--append-only-worm-style)).
Logical replication is available for the daily WORM-archive snapshot.

**Foundations-scope nuance — important.** Case-history retrieval
([§3.3.1](../../Sentinel/RFC-001-sentinel-hedgefund.md#331-stage-25--case-history-retrieval-similar-past-investigations))
is the only consumer of `pgvector` in the schema. Case-history is **out of
scope for the foundations build** — it lands in month 3, not in F1–F8.
Therefore: a missing `pgvector` extension is a **month-3 problem**, not an
F-phase blocker. Foundations need only `sentinel_app` + `sentinel_audit` with
per-database role separation; the pgvector ask can be a separate, later
request to the DBA if the shared cluster cannot host it.

This is worth flagging explicitly in the conversation so the DBA can
prioritise: per-database role separation is a Day-1 ask; `pgvector` is a "we
will need this within ~10 weeks, what's the lead time" question.

**Inputs to bring** (per [§11.4 "Things to bring"](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)):
the §12 schema (especially `case_history` with `pgvector` and `audit_log` with
WORM); ask about backup/restore SLAs and pgaudit availability.

## Options considered

- **A. Shared Postgres, two logical DBs (`sentinel_app` + `sentinel_audit`),
  pgvector enabled.** Tradeoffs: lowest infra cost; relies on the cluster
  supporting pgvector and per-database role separation; pre-existing
  backup/restore/audit tooling applies for free.
- **B. Shared Postgres without pgvector, dedicated case-history RDS.**
  Operational state and audit log on the shared cluster; case-history's vector
  index lives on a small separately-provisioned RDS-equivalent. Tradeoffs:
  two backends to operate; maintains the foundations-scope split (case-history
  is month 3 anyway); the dedicated RDS is small (~one-table workload).
- **C. Fully dedicated RDS for Sentinel.** All three databases on a
  Sentinel-owned RDS. Tradeoffs: most independence from firm DBA SLAs; loses
  the firm's standard backup / restore / audit tooling; full operational
  burden falls on the Sentinel team.

## Decision

_To be filled in after the Day-4 validation conversation with the DBA._

## Consequences

_To be filled in after the Day-4 validation conversation._

## Fallback if reversed

If the shared cluster cannot host `pgvector` (most likely flip): fall back to
**option B** — keep `sentinel_app` and `sentinel_audit` on the shared cluster
and provision a small dedicated RDS for case-history *only*. Cost per RFC
§11.4: **~2 days** for the dedicated RDS provisioning. Because case-history is
out of foundations scope, this becomes a month-3 problem, not an F-phase
blocker — F1–F8 ship unchanged, and the case-history ADR (a separate document
to be written when the case-history phase begins) records the dedicated-RDS
choice.

If the shared cluster cannot host per-database role separation either: fall
back to **option C** (fully dedicated RDS). Cost: higher than B because of
ongoing operational burden. Treat as the worst case in the umbrella D-13
fallback recorded in [ADR 0003](./0003-D13-firm-shared-infra.md).

## Validation

_To be filled in after the Day-4 validation conversation._

Expected artefacts to capture: Postgres version string; `pgvector` extension
availability (or planned-availability date); confirmation of per-database
role separation; logical-replication capability for the WORM archive snapshot;
backup/restore SLA; `pgaudit` availability; named DBA's sign-off in
`reviewers` frontmatter.

## References

- RFC §11.1 D-16: [Decisions made (with reasoning)](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)
- RFC §11.4: [First-month validation plan for tentative decisions](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)
- RFC §12.3: [What we DO store in Sentinel app DB (and why)](../../Sentinel/RFC-001-sentinel-hedgefund.md#123-what-we-do-store-in-sentinel-app-db-and-why)
- RFC §3.3.1: [Stage 2.5 — case-history retrieval (similar past investigations)](../../Sentinel/RFC-001-sentinel-hedgefund.md#331-stage-25--case-history-retrieval-similar-past-investigations)
- Foundations plan: [`docs/plans/sentinel-hedgefund-foundations.md`](../plans/sentinel-hedgefund-foundations.md), Phase F0.6
- Sibling ADRs: [0003 D-13 firm-shared infra](./0003-D13-firm-shared-infra.md) (Postgres is one of the four umbrella services)
