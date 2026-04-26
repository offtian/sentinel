# Plan: Sentinel Foundations F5 — LiteLLM proxy migration + orchestration ADR

**Status:** in-progress
**Created:** 2026-04-26
**Last updated:** 2026-04-26

## Goal

Land Phase F5 of `sentinel-hedgefund-foundations.md`: ship the LiteLLM proxy
migration (RFC §2.4) so all PydanticAI agents can route LLM calls through a
configurable network proxy via `base_url` + virtual key, while preserving
the in-process SDK fallback for local dev. Record the orchestration framework
decision (Pydantic Graph vs LangGraph) as ADR 0007 — default position is
*stay on Pydantic Graph for foundations, revisit at month 3*.

## Scope

### In scope
- ADR 0007 — orchestration framework decision (Pydantic Graph for F5–F8;
  LangGraph migration tracked under existing `pydanticai-langgraph-adoption.md`).
- `src/sentinel/domain/llm/litellm_proxy.py` — thin helper exposing
  `is_proxy_configured()` / `get_proxy_kwargs()` reading
  `litellm_base_url` + `litellm_virtual_key` from `get_config().settings`.
- Wire helper into the 5 PydanticAI agent factories (`alert_classifier`,
  `root_cause_analyser`, `k8s_investigator`, `ticket_reviewer`,
  `response_drafter`) so the `Model` constructor receives `base_url` +
  `api_key` when proxy is configured.
- Local-dev fallback: when `litellm_base_url` is unset, behave exactly as
  today (in-process LiteLLM SDK with provider keys). Emit a structured-log
  warning `litellm_proxy_disabled` at startup.
- Integration test using `pytest-httpx` (or equivalent) asserting outbound
  LLM calls flow through the proxy URL with the virtual key in the
  `Authorization` header.
- `.env.default` documenting `LITELLM_BASE_URL` + `LITELLM_VIRTUAL_KEY`,
  RFC §2.4 reference.
- `docs/architecture.md` §LLM updated with proxy-vs-SDK distinction.
- Skip-marked acceptance test scaffold for R-OB-1 egress block (full
  network-policy enforcement deferred to wk5 Helm work).

### Out of scope
- Migrating any pipeline to LangGraph (tracked under
  `pydanticai-langgraph-adoption.md`).
- Standing up an actual LiteLLM proxy in compose / Helm (deployment
  infrastructure is wk5).
- Tenant isolation / per-team virtual keys (F7 capability tokens).
- iptables egress block enforcement (wk5 Helm work — F5.7 ships only the
  documented test scaffold).

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Proxy plumbing surface | Single helper `domain/llm/litellm_proxy.py` returning kwargs dict | Avoids duplicating the conditional in each of the 5 agent factories; one place to change if API shape evolves |
| Fallback semantics | `litellm_base_url is None` → in-process SDK, no proxy kwargs | Keeps `just run-api` working locally without a proxy; matches existing dev workflow |
| Partial-config handling | `base_url` set without virtual key → treat as unconfigured + structured-log warning | Fail safe; never send unauthenticated traffic to a misconfigured proxy |
| Orchestration framework | Stay on Pydantic Graph for foundations | F4 bundle-based replay (PR #29) already meets the determinism requirement; framework swap mid-foundations would block F6/F7/F8 |
| F5.7 egress test | Skip-marked scaffold only | iptables/network-policy enforcement is wk5 Helm work; capture intent now, enforce later |
| Plan branch | Single feature branch `feat/sentinel-foundations-f5-litellm-proxy` | Strangler-style increment per umbrella plan; one PR for review |

## Steps

- [x] **F5.1** Author `docs/adrs/0007-orchestration-framework.md` — Pydantic Graph vs LangGraph; default position stay on Pydantic Graph for foundations.
- [ ] **F5.2** Create `src/sentinel/domain/llm/litellm_proxy.py` helper + unit tests; audit existing `litellm.*` call sites.
- [ ] **F5.3** Update 5 PydanticAI agent factories to pass proxy kwargs via the helper when configured; unit tests per factory.
- [ ] **F5.4** Local-dev fallback + startup structured-log warning (`litellm_proxy_disabled` / `litellm_proxy_enabled`).
- [ ] **F5.5** Integration test `tests/integration/test_litellm_proxy.py` with mocked proxy.
- [ ] **F5.6** Update `.env.default` and `docs/architecture.md` §LLM.
- [ ] **F5.7** Skip-marked acceptance-test scaffold for R-OB-1 egress block.
- [ ] Run `just lint && just test` clean; check off umbrella plan items in `sentinel-hedgefund-foundations.md` §F5.
- [ ] Open PR; update `docs/plans/INDEX.md` after merge.

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-26 | Plan created | Branch hook requires plan file before code edits |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
