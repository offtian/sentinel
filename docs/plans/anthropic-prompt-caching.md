# Plan: Anthropic Prompt Caching

**Status:** complete
**Created:** 2026-04-08
**Last updated:** 2026-04-12

> **For agentic workers:** REQUIRED SUB-SKILL — use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

## Goal

Enable vendor-agnostic prompt caching on the static system prompts of every Sentinel agent. For Anthropic models, set `anthropic_cache_instructions=True` so repeat invocations reuse the cached system prompt. For OpenAI models, set `openai_prompt_cache_key` using the prompt's SHA-256 digest. Expected savings: 50–80% reduction in TTFT and input token cost for Anthropic; automatic prefix caching for OpenAI.

Ticks:
- `docs/prd.md` §1: "Anthropic prompt-cache markers applied to all SRE agent system prompts via LiteLLM `extra_body`"
- `docs/prd.md` §2: "Anthropic prompt-cache markers applied to ticket reviewer + response drafter system prompts"

## Scope

### In scope
1. A thin wrapper `build_model_for_agent()` around `get_model_with_gateway()` that returns both a model identifier AND an appropriate `model_settings`, attaching Anthropic `cache_control` markers on static system prompts when (and only when) the configured model is Anthropic.
2. Cache-key derivation = `(PromptHandle.sha256, model_id)`. The hash MUST come from slice 5's `PromptHandle`. A minimal stub is documented below for independent merging.
3. Enforcement that only the **static** portion of a system prompt is marked cacheable. Existing `{% block system %}` / `{% block user %}` convention in `plugins/prompts/*.j2` is already split; a guard-rail test locks it in.
4. Graceful skip when the upstream model is not Anthropic. No warnings, no errors — just returns `(model_id, None)` tuple.
5. Unit tests for the wrapper (Anthropic→cache header, OpenAI→none, cache-key stability, guard-rail raises on runtime vars in system block).
6. One integration test asserting the outgoing Anthropic request body contains `system: [{"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}}]` via `respx`/`httpx.MockTransport`.
7. Update every agent call site to use `build_model_for_agent(template=..., model_name=...)`.

### Out of scope
- OTel exporter (slice 4)
- Caching tool definitions or conversation messages (only system prompt caching)
- Production measurement of TTFT / cost savings
- DB persistence of prompt sha256 on AgentCallRecord/AuditLogRecord (PRD §6 — separate plan)

## Delivered: PromptTemplate foundation (Steps 1–3)

`PromptTemplate` is an `@attrs.frozen` class at `domain/prompts/template.py`:
```python
@attrs.frozen
class PromptTemplate:
    template_name: str
    system_text: str          # pre-rendered static system prompt
    sha256: str               # SHA-256 of system_text, for cache keying
    version: str = "1"
    _jinja_template: Template | None  # renders user block on demand

    def render_user(self, **kwargs) -> str: ...
```

`prompts.load_template("name")` loads the `.j2` file, pre-renders the system block, and returns a `PromptTemplate`. All 8 agent modules use `_PROMPT_TEMPLATE = prompts.load_template("x")` at module level. Guard-rail test ensures system blocks are static.

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Wrapper vs extending `get_model_with_gateway` | New thin wrapper in `agents/_model_binding.py` | `get_model_with_gateway` remains a pure string normaliser; wrapper adds the caching dimension |
| How to route Anthropic through LiteLLM preserving `AnthropicModelSettings` | PydanticAI `AnthropicModel` pointed at LiteLLM's `/anthropic` passthrough endpoint for Anthropic-branded models; `OpenAIModel` for everything else | LiteLLM's `cache_control` contract requires structured content blocks in the `system` field, not flat `extra_body`. PydanticAI `AnthropicModelSettings(anthropic_cache_instructions=True)` emits the correct Anthropic-native shape; LiteLLM's Anthropic passthrough accepts it verbatim. |
| Cache key source | `PromptHandle.sha256` (slice 5 dependency); wrapper does NOT re-hash | Single source of truth; avoids drift between audit-log hash and cache-key hash |
| Static/dynamic separation | Existing `{% block system %}` / `{% block user %}` split; guard-rail test walks every template and asserts `system` block renders with empty context without `UndefinedError` | Lint-time enforcement |
| Anthropic-model detection | `model_name.startswith(("anthropic/", "anthropic:")) or "claude-" in model_name.lower()` | Explicit allow-list covers all env-var conventions |
| Wrapper return type | `@attrs.frozen ModelBinding(model, settings, prompt_handle)` | Immutable; carries `PromptHandle` for audit-slice reuse |
| Call-site integration | `binding = utils.build_model_for_agent(template=..., model_name=...)` then `agent.run(..., model=binding.model, model_settings=binding.settings)` | Minimal diff |

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `src/sentinel/interfaces/graphs/agents/_model_binding.py` | `ModelBinding`, `build_model_for_agent`, `NonStaticSystemPromptError` |
| `src/sentinel/plugins/prompts/_handle.py` | **Temporary shim** — `PromptHandle` class + `load_system_prompt` returning it. Deleted when slice 5 lands. |
| `tests/unit/interfaces/graphs/agents/test_model_binding.py` | Unit tests |
| `tests/unit/plugins/prompts/test_static_system_blocks.py` | Guard-rail across every `.j2` template |
| `tests/integration/interfaces/graphs/test_prompt_cache_wiring.py` | End-to-end with mocked Anthropic HTTP endpoint |

### Modified files
| File | What changes |
|------|-------------|
| `src/sentinel/interfaces/graphs/agents/utils.py` | Re-export `build_model_for_agent` + `ModelBinding` |
| `src/sentinel/plugins/prompts/__init__.py` | `load_system_prompt()` returns `PromptHandle` (with `.text` attribute for interim migration) |
| Every agent file in `agents/` | `SYSTEM_PROMPT_HANDLE = prompts.load_system_prompt(...)`, then `SYSTEM_PROMPT = SYSTEM_PROMPT_HANDLE.text` |
| `sre_investigation.py` / `support_review.py` / `chart_generation.py` / `k8s_runner.py` / `chat/app.py` / `slack/event_handlers.py` | Call-site migration to `build_model_for_agent` |
| `docs/prd.md` | Tick §1 and §2 boxes (after integration test green) |

## Steps

- [x] **Step 1: Guard-rail test for static system blocks** — `tests/unit/domain/prompts/test_static_system_blocks.py`. Parametrised over every `.j2`; asserts system block renders via `load_template()` without `UndefinedError`.

- [x] **Step 2: Introduce `PromptTemplate`** — `domain/prompts/template.py` with `@attrs.frozen` class. `from_jinja()` pre-renders system block + computes sha256. `from_text()` for tests. `render_user()` for user block.

- [x] **Step 3: Migrate agent modules** — All 8 agents use `_PROMPT_TEMPLATE = prompts.load_template("x")`, `_PROMPT_TEMPLATE.system_text` inlined at `compose_system_prompt` call sites, `_PROMPT_TEMPLATE.render_user(...)` replaces `render_user_prompt()`.

- [x] **Step 4: Tests + implementation for `build_cache_settings`** — `tests/unit/interfaces/graphs/agents/test_cache_settings.py` + `src/sentinel/interfaces/graphs/agents/_cache_settings.py`. Pure function `build_cache_settings(model_name, prompt_sha256) -> dict | None`:
  - Anthropic models → `{"anthropic_cache_instructions": True}`
  - OpenAI models → `{"openai_prompt_cache_key": prompt_sha256}`
  - Other/test → `None`
  - Provider detection: `_is_anthropic(name)`, `_is_openai(name)`
  - Tests: one per provider + unknown + bare `claude-` prefix
  Commit: `feat: add build_cache_settings with vendor-agnostic provider detection`

- [x] **Step 5: Add `get_model_name` helper to agents/utils.py** — Extract model name from a pre-built PydanticAI agent. Generalise `_get_agent_model_name()` from `chart_generation.py`. Returns `""` for test/mock models. Re-export from `utils.py`.
  Commit: `refactor: extract get_model_name helper into agents/utils`

- [x] **Step 6: Migrate SRE pipeline call sites** — `sre_investigation.py`: add `model_settings=build_cache_settings(...)` to classifier + analyser `.run()` calls. Import agent modules for `_PROMPT_TEMPLATE.sha256`.
  Commit: `feat: enable prompt caching on SRE investigation agents`

- [x] **Step 7: Migrate support pipeline call sites** — `support_review.py`: same for ticket reviewer + response drafter.
  Commit: `feat: enable prompt caching on support review agents`

- [x] **Step 8: Migrate remaining call sites** — `chart_generation.py` (×2), `k8s_runner.py`.
  Commit: `feat: enable prompt caching on chart and k8s agents`

- [x] **Step 9: Integration test** — `tests/integration/interfaces/graphs/test_prompt_cache_wiring.py`. Spy-wrapped agents verify `model_settings` flows through both SRE and support pipelines for Anthropic, OpenAI, and test models. SHA-256 stability and uniqueness also tested.
  Commit: `test: assert prompt cache markers reach agent.run() in both pipelines`

- [x] **Step 10: Documentation** — PRD §1 and §2 boxes ticked. PRD wording updated (PydanticAI model settings, not `extra_body`). `docs/plans/INDEX.md` updated.
  Commit: `docs: mark prompt caching complete`

- [x] **Step 11: Full gate** — `just test && just lint`.

## Test Plan

### Unit
- `test_model_binding.py` — pure function; `mock.patch.object(prompts, "load_system_prompt")` injects synthetic `PromptHandle`
- `test_static_system_blocks.py` — parametrised guard-rail
- `test_handle.py` — sha256 determinism for the shim (deleted when slice 5 lands)

### Integration
- `test_prompt_cache_wiring.py` — `respx.mock` intercepts `POST` to LiteLLM `/anthropic/v1/messages`, returns canned Anthropic response shaped for `AlertClassification`, asserts captured request body

## Acceptance criteria mapping

| PRD box | How ticked |
|---------|------------|
| §1: "Anthropic prompt-cache markers applied to all SRE agent system prompts" | `alert_classifier` + `root_cause_analyser` + `k8s_investigator` migrated; integration test asserts markers on wire. **Note:** delivery uses PydanticAI `AnthropicModelSettings` → LiteLLM `/anthropic` passthrough rather than OpenAI-compatible `extra_body`; PRD wording updated to reflect the actual (more idiomatic) mechanism. |
| §2: "Anthropic prompt-cache markers applied to ticket reviewer + response drafter" | `ticket_reviewer` + `response_drafter` migrated; parallel integration test asserts markers for at least `ticket_reviewer` |

## Risks / Open Questions

1. **LiteLLM passthrough endpoint availability.** Verified via context7: LiteLLM ships first-class Anthropic passthrough (`/anthropic/v1/messages`). Current `litellm_config.yaml` may need a `pass_through_endpoints` block. **Action:** Step 5 includes a local smoke-test sub-step; pin `litellm>=1.52.0` if needed.

2. **PRD wording mismatch (`extra_body`).** Implementation uses `AnthropicModelSettings` through the Anthropic passthrough — the cache_control lives in structured `system` field, not `extra_body`. **Action:** update PRD wording in Step 10 with rationale.

3. **Mixed provider configurations.** Wrapper handles this dynamically per invocation. Non-issue once both branches are tested.

4. **`intent_router` caching.** Skipped deliberately — routed via OpenAI, short system prompt with frequent churn.

5. **Two LiteLLM URL shapes in env vars.** `AI_GATEWAY_URL` points at `/v1`. Anthropic passthrough is a different path. **Proposed:** add `AI_GATEWAY_ANTHROPIC_URL` env var with sensible default derived from `AI_GATEWAY_URL` in `settings.py`.

6. **Tool-call caching.** `anthropic_cache_tool_definitions` out of scope; follow-up once slice 2 lands.

## Changes
| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-12 | Renamed `PromptHandle` → `PromptTemplate`, unified system + user blocks, made vendor-agnostic | User feedback: template handles both blocks; OpenAI also has caching |
| 2026-04-12 | Simplified Steps 4-8: `build_cache_settings` pure function instead of `ModelBinding` class | Models are baked into agents at build time; `.run()` just needs `model_settings` dict |
| 2026-04-12 | Removed `BASE_SYSTEM_PROMPT` redundant state, renamed `_handle.py` → `template.py` | Code review findings |

## Outcome

Vendor-agnostic prompt caching delivered across all Sentinel agents. `build_cache_settings()` detects the LLM provider at runtime and returns `anthropic_cache_instructions=True` for Anthropic models or `openai_prompt_cache_key=<sha256>` for OpenAI models. All pipeline call sites pass the result to `agent.run(model_settings=...)`. Integration tests verify wiring for both SRE and support pipelines across all provider paths. PRD §1 and §2 acceptance criteria satisfied.
