---
id: "0006"
title: "Agent framework: PydanticAI vs OpenAI Agents SDK (O-10, re-opens D-01)"
status: proposed
date: 2026-04-25
decision_owner: "Senior Engineer (advocate) + this engineer"
reviewers: []
rfc_refs:
  - "§11.2"
  - "§15.14"
  - "§2.3"
supersedes: null
superseded_by: null
---

# ADR 0006 — Agent framework: PydanticAI vs OpenAI Agents SDK (O-10, re-opens D-01)

## Context

Re-evaluate RFC decision **D-01** under open question **O-10**: is the agent
framework PydanticAI or OpenAI Agents SDK? RFC v0.4 reset D-01 to PydanticAI
+ LangGraph as the working choice (see header of
[`Sentinel/RFC-001-sentinel-hedgefund.md`](../../Sentinel/RFC-001-sentinel-hedgefund.md)
v0.4 changelog). This ADR records the conversation outcome.

**Scope of this ADR — important.** This document validates the *agent framework
choice* (PydanticAI vs OpenAI Agents SDK only). The *orchestration framework
choice* (Pydantic Graph vs LangGraph) is a **separate decision**, captured in
**ADR 0007** as part of phase F5 of the foundations plan. Foundations stay on
Pydantic Graph (the current codebase already uses it); LangGraph adoption is
an explicitly-deferred month-3 migration, decided later. Conflating the two
into one decision is the trap this split avoids — they have different blast
radii, different owners, and different risk profiles.

**Working assumption** (per RFC v0.4 header and
[§15.14 "Strongest recommendation"](../../Sentinel/RFC-001-sentinel-hedgefund.md#1514-agent-framework-re-evaluation-openai-agents-sdk-vs-pydanticai--langgraph)):
confirm PydanticAI for the LLM-loop. Justification: existing Sentinel codebase
already uses PydanticAI; firm has internal advocacy for it; author has prior
production experience with the closely-related Pydantic Graph variant.
LangGraph is the proposed *increment* on top, but its adoption is decided in
ADR 0007 not here.

**What hangs on this.** The agent harness build kicks off in week 2 — see
[O-10 "Decision needed by"](../../Sentinel/RFC-001-sentinel-hedgefund.md#112-open-questions-need-owners)
("Before week 2 (gates the agent harness build)"). All foundations phases
beyond F1 assume PydanticAI as the LLM-loop runtime.

**Decision criteria, in priority order** (per
[§15.14](../../Sentinel/RFC-001-sentinel-hedgefund.md#1514-agent-framework-re-evaluation-openai-agents-sdk-vs-pydanticai--langgraph)):

1. **Tool-use reliability with on-prem open models.** Run the same tool-use
   eval (BFCL + custom Sentinel tools fixture set) against Llama 3.3 70B and
   Qwen 2.5 72B through both candidate frameworks. **This is the gate.**
2. **Pipeline replay determinism.** Both can do it; cost is in lines of code.
3. **Team velocity.** PydanticAI is closer to what the author and other firm
   teams have already shipped.
4. **Compliance comfort.** PydanticAI is the better-audited choice as of the
   conversation date.

**Inputs to bring**: their POC code (if any); their tool-use eval results;
the firm's appetite for dependency-pinning on Anthropic-led OSS vs OpenAI-led
OSS; the §15.14 comparison matrix; RFC §15.15's "alternatives we rejected"
table.

## Options considered

- **A. Confirm PydanticAI + LangGraph** (RFC v0.4 default). LLM loop on
  PydanticAI; orchestration on LangGraph. Tradeoffs: matches firm advocacy;
  LangGraph checkpoint mechanism is purpose-built for replay; commits to
  LangGraph adoption that is *separately* decided in [ADR 0007](./0007-F5-orchestration-framework.md)
  (forward link — that ADR is written in F5).
- **B. OpenAI Agents SDK + plain async Python.** OpenAI-led OSS; native
  `guardrails`; first-class `handoffs`. Tradeoffs: less proven against
  on-prem open-model tool use through LiteLLM; replay requires more custom
  code; switch cost from current PydanticAI codebase is non-trivial.
- **C. PydanticAI + Pydantic Graph** (current codebase, defer LangGraph).
  Keep what is already shipped; defer LangGraph adoption to month 3 entirely.
  Tradeoffs: highest velocity in the foundations window; loses LangGraph's
  checkpoint-replay benefit until later; the LangGraph migration becomes its
  own phase F5 work item (see [ADR 0007](./0007-F5-orchestration-framework.md)).

## Decision

_To be filled in after the Day-2/Day-3 validation conversation with the
senior engineer advocating for PydanticAI + LangGraph, and the on-prem-model
tool-use eval results._

Note: regardless of which option lands here, the LangGraph-vs-Pydantic-Graph
*orchestration* call is captured separately in ADR 0007 as part of F5. This
ADR's decision narrows to "agent framework = PydanticAI" or "= OpenAI Agents
SDK".

## Consequences

_To be filled in after the Day-2/Day-3 validation conversation._

## Fallback if reversed

Switch costs (per RFC §11.4 D-01 row): **~5 days** for the harness layer if
the call is later reversed. No domain rewrite needed because both PydanticAI
and OpenAI Agents SDK speak structured outputs + tool loops; the change is
concentrated in the agent runtime adapter and the eval harness. Replay code
either gains or loses the LangGraph checkpoint hook depending on direction.

If option B (OpenAI Agents SDK) is chosen instead of A: §13.3 (OTEL trace
processor) needs the custom processor outlined in the RFC; LangGraph adoption
is moot; ADR 0007 becomes "no orchestration-framework migration" and is
closed at F5.

If option C (PydanticAI + Pydantic Graph, defer LangGraph) is chosen: ADR
0007 defers explicitly to month 3 with a clear go/no-go gate; foundations
phases F1–F8 ship without LangGraph touch.

## Validation

_To be filled in after the Day-2/Day-3 validation conversation._

Expected artefacts to capture: BFCL + custom Sentinel tool-use eval scores
for the framework choice that won, against both Llama 3.3 70B and Qwen 2.5
72B; the senior engineer's POC code (link to repo / branch); date of
conversation; named owner(s) sign-off in `reviewers` frontmatter; explicit
note on whether ADR 0007 (orchestration framework) stays scheduled for F5
or is closed early.

## References

- RFC §11.2 O-10: [Open questions (need owners)](../../Sentinel/RFC-001-sentinel-hedgefund.md#112-open-questions-need-owners)
- RFC §15.14: [Agent framework re-evaluation: OpenAI Agents SDK vs PydanticAI + LangGraph](../../Sentinel/RFC-001-sentinel-hedgefund.md#1514-agent-framework-re-evaluation-openai-agents-sdk-vs-pydanticai--langgraph)
- RFC §2.3: Agent framework — PydanticAI + LangGraph (working draft per v0.4)
- Foundations plan: [`docs/plans/sentinel-hedgefund-foundations.md`](../plans/sentinel-hedgefund-foundations.md), Phase F0.4 and Phase F5
- Sibling ADRs:
  - **ADR 0007 (forthcoming, F5):** orchestration framework — Pydantic Graph
    vs LangGraph. To be created during phase F5 of the foundations plan.
    This ADR (0006) explicitly defers the orchestration-framework decision
    to 0007 to avoid conflating two separable choices.
