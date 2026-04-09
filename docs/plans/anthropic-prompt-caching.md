# Plan: Anthropic Prompt Caching

**Status:** draft
**Created:** 2026-04-08
**Last updated:** 2026-04-08

> **For agentic workers:** REQUIRED SUB-SKILL — use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

## Goal

Enable Anthropic prompt caching on the static system prompts of every Sentinel SRE and support agent so repeat invocations of the same agent reuse the ~600-token static system prompt cache, cutting TTFT and token cost by an expected 50–80%.

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
- Skills loading (slice 1)
- `Configuration.build_mcp_toolsets()` (slice 2)
- OTel exporter (slice 4)
- `PromptHandle` class itself (slice 5) — consumed, not produced
- Caching tool definitions or conversation messages (only `anthropic_cache_instructions`)
- Production measurement of TTFT / cost savings
- Non-Anthropic provider caching

## Depends-on: slice 5 (`PromptHandle`)

Minimal contract this slice relies on:
```python
@attrs.frozen
class PromptHandle:
    template_name: str
    text: str
    sha256: str
    version: str

def load_system_prompt(template_name: str) -> PromptHandle: ...
```

**If slice 5 has not landed when this slice starts:** Step 2 introduces a *temporary* shim at `sentinel.plugins.prompts._handle` so this slice can merge independently. Slice 5 later deletes the shim and moves the class into `sentinel.plugins.prompts` without a public-API break.

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

- [ ] **Step 1: Guard-rail test for static system blocks** — `tests/unit/plugins/prompts/test_static_system_blocks.py`. Parametrise over every `.j2` in `plugins/prompts/`; render the `system` block with empty context; assert no `UndefinedError`. Commit: `test: lock in static system prompt invariant`

- [ ] **Step 2: Introduce `PromptHandle` shim** — if slice 5 hasn't landed, create `plugins/prompts/_handle.py` with the attrs class; update `load_system_prompt()` to return it. Commit: `feat: introduce PromptHandle with sha256 for prompt caching integration`

- [ ] **Step 3: Migrate existing agent modules to consume `PromptHandle`** — in each agent file, change `SYSTEM_PROMPT = prompts.load_system_prompt("x")` to `SYSTEM_PROMPT_HANDLE = prompts.load_system_prompt("x"); SYSTEM_PROMPT = SYSTEM_PROMPT_HANDLE.text`. No behaviour change. Commit: `refactor: agents carry PromptHandle alongside rendered text`

- [ ] **Step 4: Failing tests for `build_model_for_agent`** — `tests/unit/interfaces/graphs/agents/test_model_binding.py`:
  - `test_returns_anthropic_settings_for_anthropic_model`
  - `test_returns_no_settings_for_openai_model`
  - `test_detects_bare_claude_prefix`
  - `test_returned_binding_exposes_prompt_handle`
  - `test_cache_key_stable_across_invocations`
  - `test_raises_when_system_block_has_runtime_vars`
  Commit: `test: specify contract for build_model_for_agent wrapper`

- [ ] **Step 5: Implement `_model_binding.py`** — `NonStaticSystemPromptError`, `ModelBinding`, `_is_anthropic`, `build_model_for_agent`. Re-export from `agents/utils.py`. Commit: `feat: add build_model_for_agent wrapper with Anthropic cache markers`

- [ ] **Step 6: Migrate graph call sites (SRE investigation)** — `sre_investigation.py` call sites for classifier + analyser. Commit: `refactor: route SRE investigation agents through build_model_for_agent`

- [ ] **Step 7: Migrate support review call sites** — `support_review.py` for ticket reviewer + response drafter. Commit: `refactor: route support review agents through build_model_for_agent`

- [ ] **Step 8: Migrate remaining call sites** — `chart_generation.py` (×2), `k8s_runner.py`, `chat/app.py`, `slack/event_handlers.py`. Commit: `refactor: route remaining agents through build_model_for_agent`

- [ ] **Step 9: Integration test — assert markers reach the wire** — `tests/integration/interfaces/graphs/test_prompt_cache_wiring.py`:
  - Configure `alert_classifier` with an Anthropic model
  - Use `respx` to intercept `/anthropic/v1/messages`
  - Run `alert_classifier.agent.run(...)` with factory-built `Alert`
  - Assert `body["system"][0]["cache_control"] == {"type": "ephemeral"}`
  - Second test case: OpenAI model → no cache_control
  Commit: `test: assert Anthropic cache_control markers reach LiteLLM for alert classifier`

- [ ] **Step 10: Documentation sweep** — Tick PRD §1 and §2 boxes. Cross-reference this plan in `claude-plan.md` "Prompt caching" subsection. Commit: `docs: mark Anthropic prompt caching slice complete`

- [ ] **Step 11: Full gate** — `just test && just lint`. Address import-linter violations.

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

## Outcome
_Fill in after completion._
