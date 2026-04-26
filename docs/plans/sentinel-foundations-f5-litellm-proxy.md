# Plan: Sentinel Foundations F5 — LiteLLM proxy migration + orchestration ADR

**Status:** complete
**Created:** 2026-04-26
**Last updated:** 2026-04-26 (F5.5 + code-review fixes landed)

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

- [x] **F5.1** Author `docs/adrs/0007-orchestration-framework.md` — Pydantic Graph vs LangGraph; default position stay on Pydantic Graph for foundations. *(commit `da4cb9b`)*
- [x] **F5.2** Create `src/sentinel/domain/llm/litellm_proxy.py` helper + unit tests; audit existing `litellm.*` call sites. *(commit `29619fc`; audit confirmed zero direct `litellm.completion`/`acompletion` model-call sites — all routed through PydanticAI's `litellm:` prefix)*
- [x] **F5.3** Update 5 PydanticAI agent factories to pass proxy kwargs via the helper when configured; unit tests per factory. *(commit `33fbcf4`; supersedes by layering refactor below)*
- [x] **F5.4** Local-dev fallback + startup structured-log warning (`litellm_proxy_disabled` / `litellm_proxy_enabled`). *(commit `8f34595`)*
- [x] **F5.5** Integration test `tests/integration/test_litellm_proxy.py` with mocked proxy via `httpx.MockTransport` injected through `LiteLLMProvider.http_client`. Asserts the proxy URL is hit with `Authorization: Bearer <virtual_key>`. *(commit `890939e`)*
- [x] **F5.6** Update `.env.default` and `docs/architecture.md` §LLM. *(commit `a1e7166`)*
- [x] **F5.7** Skip-marked acceptance-test scaffold for R-OB-1 egress block. *(commit `3e1ad18`)*
- [x] **Layering refactor** Lift LiteLLM `Model` construction onto `CommonConfiguration._build_agent_model` per `application.md` ("Vendor adapters, agents, and infra clients live on CommonConfiguration"). Factories take a built `model` (PydanticAI `Model | str | None`) plus `skills`. *(merged via squash into commit `043974c`)*
- [x] **Code-review fixes** Convergent findings from /simplify pass: (a) bootstrap split-brain — bootstrap now reads both `litellm_base_url` and `litellm_virtual_key` and matches the helper's fail-safe semantics; (b) bootstrap uses `logs.log_event("litellm.proxy.disabled" / "litellm.proxy.enabled")` matching the dotted-event style; (c) helper consolidated to a single `get_proxy_kwargs() -> dict | None` so settings are read exactly once per call and the partial-config warning fires once per resolution. *(commits `043974c`, `f969388`)*
- [x] Run `just lint` clean (ruff + ruff format + mypy + import-linter all pass). 17 pre-existing unit-test failures are tracked separately as follow-up — none caused by F5: (i) `test_bootstrap_otel.py` and `test_config_build_mcp_toolsets.py` patch the deprecated `get_settings()` lookup (S820 settings refactor follow-up); (ii) `test_replay_cli.py` references the old `_replay_support(db=...)` signature (post-PR #29 drift); (iii) `test_config_layering.py::test_langfuse_fields_default_to_none` fails because the local `.env` populates the langfuse keys.
- [ ] Open PR; update `docs/plans/INDEX.md` after merge.

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-26 | Plan created | Branch hook requires plan file before code edits |
| 2026-04-26 | Added "Layering refactor" step | User flagged that proxy-aware Model construction + prompt template loading should live at the config layer, not in `interfaces/graphs/agents/`. f5-layering teammate dispatched. F5.5 integration test blocked on this so the test targets the final config-layer surface. |
| 2026-04-26 | /simplify reviews captured findings | Convergent across reuse + quality + efficiency reviews: (a) bootstrap split-brain — `_log_litellm_proxy_state` checks `litellm_base_url` only while helper requires both fields, so bootstrap logs `enabled` for partial config while helper falls back; (b) asymmetric logging APIs in bootstrap (`logs.get_logger().warning` vs `logs.log_event`) inconsistent with project pattern (see `utils/llm_warmup.py` for canonical dotted-event style); (c) `is_proxy_configured()` + `get_proxy_kwargs()` dual API doubles settings reads and only emits the partial-config warning from the second call. Fixes (a) + (b) apply now to files outside f5-layering's territory; (c) deferred until after f5-layering lands so the helper API change doesn't conflict with their WIP. |

## Outcome

### What was delivered
- ADR 0007 — orchestration framework decision (Pydantic Graph stays for foundations).
- `domain/llm/litellm_proxy.py` helper exposing a single `get_proxy_kwargs() -> dict | None` (settings read once per call, partial config emits structured warning).
- Proxy-aware `Model` construction lives on `CommonConfiguration._build_agent_model`, not in the interfaces layer — matches `application.md` ("vendor adapters, agents, and infra clients live on CommonConfiguration"). Five PydanticAI agent factories (`alert_classifier`, `root_cause_analyser`, `k8s_investigator`, `ticket_reviewer`, `response_drafter`) receive the built `Model` directly.
- Local-dev fallback preserved: when neither `LITELLM_BASE_URL` nor `LITELLM_VIRTUAL_KEY` is set, PydanticAI's existing in-process LiteLLM SDK path is used and bootstrap emits `litellm.proxy.disabled`. Partial config falls back to disabled with a warning.
- Settings: empty-string env vars (`LITELLM_BASE_URL=`, `OTEL_COLLECTOR_ENDPOINT=`, `LANGFUSE_HOST=`, plus the SecretStr counterparts) are coerced to `None` via a `field_validator` so unset knobs follow the documented fallback path (the local `.env` ships these as empty by convention).
- F5.5 integration test using `httpx.MockTransport` injected through `LiteLLMProvider.http_client`: asserts proxy URL hit with `Authorization: Bearer <virtual_key>` end-to-end through `alert_classifier.build_agent`.
- F5.7 R-OB-1 acceptance scaffold (skip-marked) — full iptables egress block enforcement is wk5 Helm work.
- `.env.default` documents `LITELLM_BASE_URL` + `LITELLM_VIRTUAL_KEY` with RFC §2.4 reference; `docs/architecture.md` §LLM updated.

### Follow-up / tech debt
- 17 pre-existing unit-test failures left for a separate follow-up PR (none caused by F5):
  - `test_bootstrap_otel.py` (9) — patches the deprecated `get_settings()` lookup; needs migration to the module-level singleton pattern from `tests/conftest.py::patch_settings`.
  - `test_config_build_mcp_toolsets.py` (3) — expects bare `MCPServerSSE`, but post-F4 replay merge wraps toolsets in `ReplayCapturingToolset`.
  - `test_replay_cli.py` (4) — references the old `_replay_support(db=...)` signature that was removed in PR #29.
  - `test_config_layering.py::test_langfuse_fields_default_to_none` (1) — local `.env` populates the langfuse keys so the unit test sees non-`None` values.
- Open the F5 PR and update `docs/plans/INDEX.md` once merged.
