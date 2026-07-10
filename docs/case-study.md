# Case Study: Sentinel — When the Map Beat the Territory

*Written 2026-07-07, at the close-out of this repository. The evidence behind every claim here is
in the [blind-spot review](reviews/2026-07-06-blind-spot-review.md); this document is the short
version of the arc.*

## TL;DR

I built an AI SRE platform on the conviction that a production agent is only trustworthy inside a
**deterministic orchestration graph** wrapped in contract layers — pre-scripted runbooks, capability
tokens, byte-identical replay, a groundedness gate. Roughly 34k lines of source, 41k lines of tests,
and eight merged foundation plans later, production experience delivered the verdict: a **minimal
Claude Agent SDK loop** — bash + PromQL + Loki behind an always-on, read-only, fail-closed guardrail —
produced materially better investigations than the entire apparatus in this repo. The graph wasn't
the safety layer; it was the ceiling. This repo is preserved as the honest "before" picture, and this
page is what I learned.

## The bet

Sentinel automates two on-call workflows: investigating production alerts (PagerDuty/Datadog →
root-cause findings posted to Slack) and drafting responses to support tickets (Jira → suggested
replies with documentation citations). The stakes framing was a compliance-heavy environment: human
approval gates, audit trails, reproducibility.

The design conviction underneath it: *LLM agents become production-trustworthy through deterministic
structure.* Concretely —

- A **fixed pipeline** (`classify → match_runbook → investigate → analyse_root_cause →
  determine_confidence → approval gate → publish`), first as a Pydantic Graph DAG, later
  re-implemented on LangGraph with interrupt-based human approval.
- **Runbook contracts** (F6): a content-hashed runbook catalog with a three-stage matcher
  (deterministic tags → small-LLM disambiguator → opt-in RAG fallback), pre-scripting the
  investigation for known alert categories.
- **Capability tokens** (F7): per-runbook tool grants enforced at the toolset boundary, with
  cross-tenant rejection and audit rows.
- **Deterministic replay** (F4): every run captured as a canonical-JSON `ReplayBundle` (tool I/O +
  LLM I/O + SHA-256), re-executable bit-for-bit, defended by a 30-run determinism CI job.
- **A groundedness gate** (F8): findings must cite evidence or the run is forced to human review.

## What got built

The eight foundation plans (F1–F8) all shipped: layered configuration, an identity envelope
propagated through every span and log line, OTel → Langfuse tracing with nine mandatory span
attributes enforced by a validator, the replay bundle machinery, in-process LiteLLM model routing,
the runbook catalog and matcher, capability tokens, and the groundedness gate. Around them: a
PostgreSQL job queue (`FOR UPDATE SKIP LOCKED`, job timeouts, bounded retries), enforced layer
boundaries via import-linter contracts, typed observability models shared by both orchestration
frameworks, and ~1,600 test functions.

The craft was real. The direction was the problem.

## The reality check

The production system (private, work context — not this repo) needed the same investigation
capability. Rather than port this codebase, the investigation agent was rebuilt on the **Claude
Agent SDK**: a free agent loop with three tools — bash, PromQL, Loki — behind a blanket read-only
guardrail, always on, fail-closed. No graph, no runbook matcher, no capability tokens, no replay.

It out-investigated this repo's pipeline decisively. Two findings explain why, and they reframe the
whole project:

1. **The graph caps the agent's intelligence.** Hard-coded edges mean the agent cannot decide
   "metrics look clean — let me re-query the logs with a different filter." Every place the pipeline
   pre-scripted the investigation (the runbook matcher most of all) was a place the agent was
   prevented from being smart. The orchestration I built as the *safety* layer was functioning as
   the *capability ceiling*.

2. **Compliance is orthogonal to orchestration — and I had conflated them.** The parts of Sentinel
   worth keeping (append-only audit log, human approval gate, per-call trace/token capture,
   read-only tool enforcement) wrap a free loop just fine. The parts that existed *because of* the
   graph (byte-identical replay, per-runbook capability grants, fixed node sequencing) die with it —
   deterministic replay is *actively incompatible* with a loop that is non-deterministic by design.

A third, smaller lesson compounds the first two: **simple-and-always-on beats
elaborate-and-conditional.** This repo's `RunbookScopedToolset` — the elaborate version — failed
open on the no-runbook path (a supported, common flow) and didn't cover MCP toolsets at all. The
production guardrail — a blunt read-only check at the tool boundary — has no such gaps because it
has no conditions.

## The blind-spot review

Before retiring the repo I commissioned an adversarial
[blind-spot review](reviews/2026-07-06-blind-spot-review.md) — explicitly hunting unknown
unknowns, with every claim verified against source. Beyond the headline map-vs-territory finding,
the most instructive results:

- **The quality loop was largely cosmetic.** The "multi-factor" confidence score reduced to 30%
  finding-count + 50% the LLM grading itself + 20% a hardcoded constant. The eval suite mocked both
  the agents and the judge, wasn't in CI, and had been silently import-broken for weeks.
- **The compliance centrepiece couldn't fire.** The human-approval gate paused investigations but
  notified nobody (the Slack approval sender had zero call sites), the approver identity was a
  free-text string on an unauthenticated endpoint, and the groundedness check was vacuously
  satisfied by construction.
- **Nothing had touched real data.** All development ran against synthetic alerts and tickets;
  no real incident ever went through an unmocked agent in this repo.
- **The docs described a pipeline that didn't run by default.** The LangGraph migration stopped
  mid-cutover behind a flag that defaulted off, while three top-level docs described it as current.

None of this was sloppiness in the small — tests were green, layers were enforced, spans were
validated. It was the predictable result of building a large deterministic *map* ahead of ever
walking the *territory* with a real model on real incidents. Production walked it and reported back.

## Principles carried forward

These are the transferable lessons the successor build starts from — the principle, not the code:

- **Guardrails: read-only at the tool boundary, always on, fail-closed.** Never conditional,
  never per-runbook, never fail-open.
- **Compliance wraps the loop; it doesn't constrain it.** Append-only audit log, human approval
  gate, per-call trace/token capture around a free agent loop. No deterministic replay, no
  capability tokens, no orchestration graph.
- **Authenticated ingress and accountable approver identity are the floor**, not a follow-up.
  An approval gate that accepts a self-asserted name is theatre.
- **A human gate must actually notify a human** — wire the notification into the pause path on
  day one, and reap stale work by *time*, not worker identity.
- **Never run the agent loop inline in a request handler**; hand off to a queue so a burst can't
  starve health checks. Make outbound posts idempotent so retries can't double-page on-call.
- **Measure the core on real data before building the scaffolding.** One thin eval on real
  incidents with unmocked agents, gating prompt/model changes in CI, beats an eval framework that
  mocks the model.
- **Confidence must come from evidence, not self-report.** An LLM grading its own answer is not a
  calibration signal, and a constant is not a recency factor.
- **Quarantine all untrusted content in prompts** — alerts, tickets, logs, and fetched docs, not
  just the one content class you thought of first.

## What's still worth reading here

For code-readers, the parts that hold up:

- **The job queue** (`src/sentinel/worker.py`, `domain/jobs/`) — Postgres `FOR UPDATE SKIP LOCKED`,
  real asyncio timeouts, bounded retries.
- **Interrupt-based approval** (`interfaces/workflows/sre_investigation.py`) — LangGraph
  `interrupt()` + `AsyncPostgresSaver` so a pending approval survives a worker restart.
- **Typed observability** (`utils/observability/`) — one set of span-attribute models shared by two
  orchestration frameworks, validated at export.
- **Layer enforcement** — import-linter contracts in `pyproject.toml` that actually kept the
  layering honest for the life of the project.

And the parts to read as cautionary exhibits, documented rather than fixed: the fail-open runbook
scoping, the self-report confidence score, the mocked eval framework, the A/B comparison mode that
never ran both backends, and the approval gate that never paged anyone. The
[blind-spot review](reviews/2026-07-06-blind-spot-review.md) indexes all of them with file-and-line
citations.

## The successor

The lessons above are being rebuilt as a small, public, greenfield repo: a minimal Claude Agent SDK
investigation loop behind an always-on read-only guardrail, wrapped by the separable compliance
layer (audit log, notifying approval gate, trace/token capture, authenticated ingress, idempotent
outbound posts) and a thin unmocked eval on real incident fixtures.

> **Successor repo:** link forthcoming — this page and the README will be updated when it is public.
