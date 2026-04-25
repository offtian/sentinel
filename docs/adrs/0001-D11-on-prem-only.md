---
id: "0001"
title: "On-prem-only LLMs via firm LiteLLM proxy (D-11)"
status: proposed
date: 2026-04-25
decision_owner: "Head of Compliance / Risk Officer responsible for LLM policy"
reviewers: []
rfc_refs:
  - "§11.1"
  - "§11.4"
  - "§2.4"
supersedes: null
superseded_by: null
---

# ADR 0001 — On-prem-only LLMs via firm LiteLLM proxy (D-11)

## Context

Validate RFC decision **D-11**: all Sentinel LLM calls route through the firm's
LiteLLM proxy and target only on-prem vLLM endpoints. No external providers.

This is a *tentative* decision in the RFC ([§11.1 D-11](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning))
— ratified in week 2 from a reading of firm policy, sufficient to start
implementation but not committed. The Day-2 conversation with the named owner
is what moves it from tentative to confirmed, or flips it to one of the
fallback shapes.

**Working assumption.** Zero LLM data egress. The case-history retriever, the
investigator agent, and the redactor judge all hit the firm-shared LiteLLM
proxy, which routes to vLLM clusters running inside the firm perimeter
(Llama 3.3 70B Instruct, Qwen 2.5 72B Instruct, DeepSeek-V3 are the candidate
on-prem models per [§2.4](../../Sentinel/RFC-001-sentinel-hedgefund.md#24-litellm-proxy-as-the-llm-chokepoint--on-prem-only-d-11)).

**What hangs on this.** D-11 sets the model allowlist surface for the entire
foundations build. Phase F1.6 (`SRETeamConfig.model_id_primary`) assumes only
on-prem identifiers (`litellm:llama-3.3-70b-instruct` etc.). Phase F4
observability assumes per-tenant tags travel with every LLM call but never
leave the firm boundary. The week-1–2 tool-use eval ([§2.4 closing paragraph](../../Sentinel/RFC-001-sentinel-hedgefund.md#24-litellm-proxy-as-the-llm-chokepoint--on-prem-only-d-11))
picks the smallest on-prem model that hits ≥85% on BFCL + custom Sentinel
tools — that result drives the allowlist this ADR ratifies.

**Inputs to bring to the conversation** (per
[§11.4 "Things to bring"](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)):
the §8 risk register, the §3 PII classes, and the redactor design — frame the
question as "is the redactor sufficient?" alongside "are external providers
acceptable?" so the answer can be yes-with-conditions.

## Options considered

- **A. Confirm on-prem-only.** Allowlist restricted to vLLM-served Llama / Qwen /
  DeepSeek models behind the firm's LiteLLM proxy. Tradeoffs: lower model
  quality than frontier closed models; absorbed by more rigorous prompt
  engineering and tool-use evals. Zero LLM data egress; cleanest compliance
  story.
- **B. External providers acceptable for non-MNPI classes, with conditions.**
  E.g. Anthropic via dedicated VPC endpoint for `pii_class in {public, internal}`,
  on-prem only for `confidential` and `mnpi`. Tradeoffs: model-quality lift on
  the easy classes; introduces a classification gate that must never
  mis-classify; redactor's role broadens.
- **C. Hybrid with classification gating.** Per-call classifier picks on-prem
  vs external based on payload PII class; LiteLLM enforces the routing.
  Tradeoffs: most flexible; biggest blast radius if classifier mis-fires;
  most-complex audit story.

## Decision

_To be filled in after the Day-2 validation conversation with the named owner._

## Consequences

_To be filled in after the Day-2 validation conversation._

## Fallback if reversed

If the conversation flips to **option B** (external+VPC OK for non-MNPI):
relax the model allowlist, swap the on-prem vLLM for Anthropic-via-Bedrock for
the *investigator only*, keep redactor + judge on-prem. Cost per RFC §11.4:
**~3 days**. The change is concentrated in `SRETeamConfig.model_id_primary`,
the LiteLLM virtual-key allowlist, and the OTEL collector's per-tenant routing
tags. Foundations phases F1–F4 stay unchanged in shape.

If the conversation flips to **option C** (hybrid classification): not adopted
in foundations — defer to month 3, document as a follow-up amendment. The PII
classifier itself is out of foundations scope.

## Validation

_To be filled in after the Day-2 validation conversation._

Expected artefacts to capture: signed firm LLM-use policy document; the
approved on-prem model list; the LiteLLM virtual-key request form (model
allowlist as ratified above); date of conversation; named compliance owner's
sign-off recorded in the `reviewers` frontmatter.

## References

- RFC §11.1 D-11: [Decisions made (with reasoning)](../../Sentinel/RFC-001-sentinel-hedgefund.md#111-decisions-made-with-reasoning)
- RFC §11.4: [First-month validation plan for tentative decisions](../../Sentinel/RFC-001-sentinel-hedgefund.md#114-first-month-validation-plan-for-tentative-decisions)
- RFC §2.4: [LiteLLM proxy as the LLM chokepoint — on-prem only (D-11)](../../Sentinel/RFC-001-sentinel-hedgefund.md#24-litellm-proxy-as-the-llm-chokepoint--on-prem-only-d-11)
- Foundations plan: [`docs/plans/sentinel-hedgefund-foundations.md`](../plans/sentinel-hedgefund-foundations.md), Phase F0.3
- Sibling ADRs: [0003 D-13 firm-shared infra](./0003-D13-firm-shared-infra.md) (LiteLLM proxy operator slice)
