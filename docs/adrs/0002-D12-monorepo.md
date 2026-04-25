---
id: "0002"
title: "Sentinel codebase as sub-package in firm platform monorepo (D-12)"
status: proposed
date: 2026-04-25
decision_owner: "Tech Lead, Platform Engineering"
reviewers: []
rfc_refs:
  - "§11.1"
  - "§11.4"
supersedes: null
superseded_by: null
---

# ADR 0002 — Sentinel codebase as sub-package in firm platform monorepo (D-12)

## Context

Validate RFC decision **D-12**: Sentinel ships as a sub-package inside the
firm's platform-engineering monorepo, conforming to its CI/CD, lint, type-check,
and review conventions.

This is a *tentative* decision in the RFC ([§11.1 D-12](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning))
pending the Day-1 onboarding conversation with the named owner. The current
greenfield repo (this directory) was the pragmatic week-2 choice; the sprint
question is whether to migrate into the monorepo or stay standalone.

**Working assumption.** Sub-package style — Sentinel benefits from shared
libraries, shared CI / lint / type-check infra, and standard monorepo-wide
review. The firm already runs a platform monorepo and has conventions for
where new platform services land.

**What hangs on this.** F1 phase 1 (config refactor) is mostly insensitive to
repo shape, but the F1 acceptance criteria (import-linter contracts, package
layout) need to know whether `sentinel.*` lives at the package root or under a
firm-wide namespace prefix. If migrate, the import paths in the foundations
plan's "Files this plan touches" listing
([`sentinel-hedgefund-foundations.md` lines ~180–215](../plans/sentinel-hedgefund-foundations.md))
prepend the firm prefix.

**Inputs to bring** (per [§11.4 "Things to bring"](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)):
the §6 deployment topology and the §14 plan; ask about firm conventions for
cross-cutting platform services and whether they prefer one-service-per-repo or
sub-package style for new platforms.

## Options considered

- **A. Sub-package in monorepo.** Sentinel lives under the firm's platform
  monorepo (e.g. `<monorepo>/services/sentinel/`). Tradeoffs: shared libraries,
  shared CI; slower per-PR cycle for monorepo-wide review; conformance with
  firm lint/type-check rules out of the box.
- **B. Standalone repo.** Sentinel keeps its own repo (current state).
  Tradeoffs: faster iteration for the small team; have to recreate the firm's
  CI/lint/type-check rules locally; harder to share libraries; some firms
  prefer this for risky/new code.
- **C. Hybrid — separate repo, mirror to monorepo.** Develop in the standalone
  repo; nightly mirror to monorepo for dependency consumers. Tradeoffs: doubles
  the surface area; only worth it if the firm requires monorepo presence for
  release tooling but resists day-to-day commits there.

## Decision

_To be filled in after the Day-1 validation conversation with the named owner._

## Consequences

_To be filled in after the Day-1 validation conversation._

## Fallback if reversed

If the conversation flips to **option B** (standalone repo confirmed as
firm-normal for new platforms): keep the current repo shape, recreate the
firm's CI/lint/type-check config locally. Cost per RFC §11.4: **~half a day** of
CI scaffolding delta, mostly translating the firm's standard pipeline to
GitHub Actions / similar.

If the conversation lands on **option C** (mirror): treat as a process question
for month 2+, not a foundations blocker. The mirror tooling is independent of
the codebase shape.

## Validation

_To be filled in after the Day-1 validation conversation._

Expected artefacts to capture: link to the firm's monorepo onboarding doc; the
example service the platform tech lead pointed at as a model; CI/CD pipeline
config; lint / type-check rule set; migration plan (if option A) including
which week to land it; named owner's sign-off in `reviewers` frontmatter.

## References

- RFC §11.1 D-12: [Decisions made (with reasoning)](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)
- RFC §11.4: [First-month validation plan for tentative decisions](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)
- Foundations plan: [`docs/plans/sentinel-hedgefund-foundations.md`](../plans/sentinel-hedgefund-foundations.md), Phase F0.2
- Sibling ADRs: [0003 D-13 firm-shared infra](./0003-D13-firm-shared-infra.md) (the monorepo decision and the shared-infra decision are independent — sub-package style does not imply infra reuse, and vice versa)
