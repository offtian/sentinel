# Plan: Universal MCP Injection

**Status:** draft
**Created:** 2026-04-08
**Last updated:** 2026-04-08

## Goal

Make `MCP_SERVERS` the single source of truth for shared MCP toolsets across every non-router pipeline agent in Sentinel. Adding a Datadog/GitHub/Confluence MCP server (or any future MCP server) becomes a single env var change — no code edits in any agent, pipeline, worker, or chat surface.

This ticks the PRD section 1, 2, and 7 boxes covering "SRE/Support agents auto-load MCP toolsets discovered from `MCP_SERVERS`", "`Configuration.build_mcp_toolsets()` is the single place that builds the shared MCP toolset list", and "`MCP_SERVERS` documented in `.env.default` with examples".

## Scope

### In scope
- New memoised method `Configuration.build_mcp_toolsets()` on `src/sentinel/config.py` that wraps `plugins.toolsets.mcp.build_mcp_toolsets` exactly once per process and is the only caller of that builder going forward (besides tests).
- Wire shared MCP toolsets into the runtime invocations of every non-router pipeline agent: `alert_classifier`, `root_cause_analyser`, `ticket_reviewer`, `response_drafter`, `chart_generator`. Wiring lives at the `agent.run(toolsets=...)` call sites in `interfaces/graphs/sre_investigation.py`, `interfaces/graphs/support_review.py`, and `interfaces/graphs/chart_generation.py`, fed via the existing `analyser_toolsets` / `reviewer_toolsets` / `drafter_toolsets` Dependencies fields plus two new ones (`classifier_toolsets` for alert classifier and `chart_generator_toolsets` for chart generation).
- Update `worker.py` (`_run_sre_investigation`, `_run_support_review`) and any chat / supervisor / API entry points so shared MCP toolsets flow from `Configuration.build_mcp_toolsets()` into each pipeline.
- Document `MCP_SERVERS` in `.env.default` with three commented worked examples (Datadog MCP HTTP, GitHub MCP HTTP, Confluence MCP stdio).
- Graceful degrade: empty/malformed `MCP_SERVERS` and unreachable servers must NOT break agent runs — preserve "vendor adapters no-op when unconfigured" semantics.
- Unit tests for the memoised builder (empty, malformed JSON, single SSE, single stdio, multiple mixed) plus an integration regression test asserting (a) every listed agent receives the shared toolsets and (b) `k8s_runner.run_k8s_agent` mounts each shared MCP server exactly once.

### Out of scope
- Skills loading and the FastMCP `list_skills` tool (slice 1).
- Anthropic prompt-cache markers (slice 3).
- OTLP exporter / Logfire / Datadog APM wiring (slice 4).
- Prompt versioning, hashing, replay snapshots (slice 5).
- Adding new MCP servers themselves — this slice only wires the injection *path*.
- Changing the JSON schema parsed by `plugins.toolsets.mcp.build_mcp_toolsets` — reuse as-is.
- Modifying `intent_router` (explicitly excluded) or `chart_request_parser` (deterministic structured-output parser, no tool calls).

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Where shared MCP toolsets are built | New method `Configuration.build_mcp_toolsets()` on `config.py` | PRD names this exact method. `Configuration` already wires every other adapter. |
| Memoisation strategy | Cache tuple as private `_mcp_toolsets: tuple[Any, ...] \| None` field on the `Configuration` instance, populated on first call | Mirrors existing `get_config()`/`get_settings()` per-process singletons. Per-instance caching enables test isolation without a `reset_*()` helper. |
| Thread-safety | Wrap lazy init in a module-level `threading.Lock` (`_mcp_build_lock`) | Worker is asyncio single-threaded, but FastAPI lifespan, eval runner, and Streamlit chat can race on first call. Cheap and conservative. |
| Test isolation | Tests build fresh `Configuration(settings=Settings(mcp_servers="..."))` and never touch `get_config()` | Same pattern existing tests use for `build_holmes_adapter`. |
| Env var schema | Reuse existing JSON list format already parsed by `plugins.toolsets.mcp.parse_mcp_server_configs` (`[{"name", "url"}]` for SSE / `[{"name", "command", "args"}]` for stdio) | The parser exists; schema change would force the K8s path to migrate. |
| Toolset ordering | **Per-agent first, shared MCP second**: `[*per_agent, *shared_mcp]` | PydanticAI resolves duplicate tool names by first-wins. Per-agent tools are tightly scoped and vetted; shared MCP is user-configured and might shadow accidentally. Documented and tested. |
| Graceful degrade on empty config | `build_mcp_toolsets()` returns `()`; call sites pass `toolsets=list(per_agent) or None` | Already the existing pattern. |
| Graceful degrade on unreachable server | Do NOT eagerly connect in `build_mcp_toolsets()`. PydanticAI MCP servers connect lazily; existing per-node `try/except` blocks handle failures. | Eager connect would couple worker startup to vendor MCP availability. |
| K8s agent double-mount avoidance | Refactor `build_k8s_investigation_adapter` to call `self.build_mcp_toolsets()` instead of calling `mcp_toolset_mod.build_mcp_toolsets(...)` directly, so both routes share the same memoised tuple. | Single source; regression test asserts no duplication. |
| Two new Dependencies fields (`classifier_toolsets`, `chart_generator_toolsets`) | Follow existing `analyser_toolsets` / `reviewer_toolsets` / `drafter_toolsets` pattern | Uniform wiring; avoids hard-coding `cfg` inside graph nodes. |

## Steps

### Step 1 — Tests for `Configuration.build_mcp_toolsets()`
- [ ] **1a:** Add `tests/unit/test_config.py::TestBuildMcpToolsets`:
  - `test_returns_empty_tuple_when_mcp_servers_unset`
  - `test_returns_empty_tuple_when_mcp_servers_malformed_json`
  - `test_builds_single_sse_server_from_url`
  - `test_builds_single_stdio_server_from_command`
  - `test_builds_multiple_mixed_servers_in_declaration_order`
  - `test_memoises_result_across_calls` (assert identity)
  - `test_thread_safe_under_concurrent_first_call` (8-worker ThreadPoolExecutor)
  - `test_two_configurations_have_independent_caches`
- [ ] **1b:** Run the file — expect failures.

### Step 2 — Implement `Configuration.build_mcp_toolsets()`
- [ ] **2a:** In `config.py`, add private `_mcp_toolsets` field and module-level `_mcp_build_lock = threading.Lock()`.
- [ ] **2b:** Add `def build_mcp_toolsets(self) -> tuple[object, ...]` populating `self._mcp_toolsets` under the lock.
- [ ] **2c:** Refactor `build_k8s_investigation_adapter` to call `self.build_mcp_toolsets()`. `K8S_MCP_SERVER_URL` fall-through stays as a K8s-only extra `MCPServerSSE` appended after the shared tuple — **not** double-mounted into the shared cache.
- [ ] **2d:** Run tests; all pass.

### Step 3 — Tests for pipeline-level wiring
- [ ] **3a:** Extend `test_sre_investigation.py` with `test_classify_alert_passes_shared_mcp_toolsets` and `test_analyse_root_cause_composes_per_agent_then_shared_toolsets_in_order`
- [ ] **3b:** Extend `test_support_review.py` analogously
- [ ] **3c:** Extend `test_chart_generation.py` with `test_chart_generator_run_includes_shared_mcp_toolsets`

### Step 4 — Add `classifier_toolsets` and pipeline plumbing for SRE
- [ ] **4a:** In `sre_investigation.py`:
  - Add `classifier_toolsets: Sequence[AbstractToolset[object]] = ()` to `Dependencies` and `investigate_alert()`
  - Pass `toolsets=list(ctx.deps.classifier_toolsets) or None` into `alert_classifier.agent.run(...)` in `ClassifyAlert`
  - Document the per-agent-first / shared-second composition rule in comments
- [ ] **4b:** Run pipeline tests — green.

### Step 5 — Add `chart_generator_toolsets` plumbing
- [ ] **5a:** In `chart_generation.py`, add `chart_generator_toolsets` parameter to `_run_chart_generator()` and `generate_chart()`; thread into `chart_generator.agent.run(...)`.
- [ ] **5b:** Run chart_generation tests — green.

### Step 6 — Wire `Configuration.build_mcp_toolsets()` into worker entry points
- [ ] **6a:** In `worker.py::_run_sre_investigation`:
  - `shared_mcp = cfg.build_mcp_toolsets()`
  - `analyser_toolsets=(observability_toolset, *shared_mcp)`
  - `classifier_toolsets=shared_mcp`
- [ ] **6b:** In `worker.py::_run_support_review`:
  - `reviewer_toolsets=(cfg.build_ticket_triage_toolset(), *shared_mcp)`
  - `drafter_toolsets=(cfg.build_support_search_toolset(), *shared_mcp)`
- [ ] **6c:** Audit other entry points (Streamlit chat `interfaces/chat/app.py`, supervisor orchestrator, eval runner) and route shared MCP toolsets through them too.
- [ ] **6d:** Run unit + integration test suites.

### Step 7 — Document `MCP_SERVERS` in `.env.default`
- [ ] **7a:** Append `# MCP Servers` section with three commented examples (Datadog HTTP, GitHub HTTP, Confluence stdio) and a note that omitting the var disables shared MCP injection without breaking agents.

### Step 8 — Regression integration test for K8s no-double-mount
- [ ] **8a:** Add `tests/integration/test_universal_mcp_injection.py`:
  - Build `Configuration(settings=Settings(mcp_servers='[{"name":"dd","url":"..."}]', k8s_investigation_backend="native", k8s_mcp_server_url="..."))`
  - `cfg.load_vendors()`; build K8s adapter via `cfg.build_k8s_investigation_adapter()`
  - Patch `k8s_investigator.agent.run` to capture `toolsets=` kwarg
  - Assert Datadog MCP server appears **exactly once** plus K8s function toolset plus `K8S_MCP_SERVER_URL` SSE server
- [ ] **8b:** Add `test_pipeline_agents_receive_shared_mcp_toolsets` asserting all five non-router agents get the shared MCP server exactly once.

### Step 9 — Lint, full test run, doc sync
- [ ] **9a:** `just lint` — fix ruff/mypy/import-linter issues
- [ ] **9b:** `just test && just test-integration`
- [ ] **9c:** Tick PRD boxes (sections 1, 2, 7)
- [ ] **9d:** Run `/update-docs`

## Files to create / modify

### Modify
| File | What changes |
|------|-------------|
| `src/sentinel/config.py` | Add `_mcp_toolsets` field, module-level lock, `build_mcp_toolsets()` method. Refactor `build_k8s_investigation_adapter`. |
| `src/sentinel/interfaces/graphs/sre_investigation.py` | Add `classifier_toolsets`; thread into `ClassifyAlert.agent.run`. |
| `src/sentinel/interfaces/graphs/support_review.py` | Document toolset ordering above existing `agent.run` calls. |
| `src/sentinel/interfaces/graphs/chart_generation.py` | Add `chart_generator_toolsets` parameter. |
| `src/sentinel/worker.py` | Build `shared_mcp = cfg.build_mcp_toolsets()` and pass through. |
| `src/sentinel/interfaces/chat/app.py` | Same wiring. |
| `src/sentinel/application/supervisor/orchestrator.py` (if it calls pipelines) | Same wiring. |
| `src/sentinel/evals/runner.py` | Same wiring so evals see the same shared MCP environment. |
| `.env.default` | Add `MCP_SERVERS` section. |

### Create
| File | Responsibility |
|------|----------------|
| `tests/unit/test_config.py` (extend) | `TestBuildMcpToolsets`. |
| `tests/integration/test_universal_mcp_injection.py` | K8s no-double-mount + all-agents-wired. |

No new production source files.

## Test Plan

### Unit
- `TestBuildMcpToolsets` covers parser pass-through, memoisation identity, thread safety, test isolation.
- Pipeline unit tests extended with ordering assertions using `mock.patch.object(<module>, "agent")`.

### Integration
- `test_k8s_agent_mounts_shared_mcp_servers_exactly_once` — regression guard
- `test_all_pipeline_agents_receive_shared_mcp_toolsets` — happy path
- `test_empty_mcp_servers_does_not_break_pipelines`
- `test_malformed_mcp_servers_does_not_break_pipelines`

## Acceptance criteria mapping

| PRD § | Checkbox | Satisfied by |
|-------|----------|--------------|
| 1 | "SRE agents auto-load MCP toolsets from `MCP_SERVERS`" | Steps 4 + 6a |
| 2 | "Support agents auto-load MCP toolsets from `MCP_SERVERS`" | Step 6b |
| 7 | "`Configuration.build_mcp_toolsets()` is the single place..." | Step 2 + 2c |
| 7 | "`MCP_SERVERS` documented in `.env.default`" | Step 7 |

## Risks / open questions

| Risk | Mitigation |
|------|------------|
| Duplicate tool names between per-agent toolset and user-configured MCP server | Ordering rule + explicit unit test |
| Vendor MCP server unreachable at runtime | PydanticAI lazy connect + existing exception handlers; no new failure modes |
| Memoisation hides config changes during long-lived processes | Hot-reload needs worker restart — same as `OBSERVABILITY_BACKEND` |
| Test isolation with cached tuple | Fresh `Configuration` per test; verified by `test_two_configurations_have_independent_caches` |
| Should eval runner see same MCP servers as production? | Default yes; divergence would invalidate eval results |

## Changes
| Date | What changed | Why |
|---|---|---|

## Outcome
_Fill in after completion._
