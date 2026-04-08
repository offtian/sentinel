# Plan: OTel Telemetry Exporter

**Status:** draft
**Created:** 2026-04-08
**Last updated:** 2026-04-08

## Goal

Close the observability loop in Sentinel's Agent Capability Platform by exporting PydanticAI's existing `instrument=True` spans through an OTLP traces pipeline (Logfire for local dev, Datadog APM / generic OTLP collector in production), ensure every graph node is persisted via `ExecutionTracer`, and capture token/cost per agent call plus skill activations into the audit log. Delivers the four "Observability & Feedback Loop" boxes in PRD §4 and the `bootstrap.initialise()` OTLP criterion in §7.

## Scope

### In scope
1. **OTLP traces exporter** wired into app startup.
   - Dev path: `logfire.configure()` when `LOGFIRE_TOKEN` is set.
   - Prod path: `OTLPSpanExporter` (HTTP/protobuf) pointed at Datadog's OTLP intake (or a generic collector) when `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is set.
   - Picks up PydanticAI's existing `Agent(..., instrument=True)` spans without touching agent modules.
   - Idempotent — second call is a no-op.
2. **Startup wiring** in every entry point: FastAPI `lifespan`, `worker.py` main, Streamlit chat startup, `evals/` runner.
3. **Per-pipeline-run snapshot persistence** — ensure every graph node in `sre_investigation.py`, `support_review.py`, `chart_generation.py` goes through `ExecutionTracer.start_node` / `complete_node` (currently only `start_pipeline`/`complete_pipeline` are called by nodes — `persist_node_execution` is not being called from any node).
4. **Token/cost per agent call** — extract `usage.input_tokens`, `usage.output_tokens`, `usage.total_tokens`, and a best-effort cost estimate from PydanticAI `AgentRunResult.usage()` and persist through `ExecutionTracer.record_agent_call` (→ `persist_agent_call` → existing `AgentCallRecord.token_usage_json`). No new schema.
5. **Skill activations audit persistence** — new `record_skill_activation_audit` call in the supervisor decision step that writes via existing `record_audit_entry` with `action="skill.activated"`. Slice 1 owns the structlog emission; this slice owns the DB write.
6. **Unit tests** — bootstrap selects correct exporter based on env vars, no-ops gracefully, is idempotent.
7. **Integration test** — full `investigate_alert` run produces `pipeline_runs` + one `node_executions` per node + one `agent_calls` per agent invocation with non-null `token_usage_json`. Uses `InMemorySpanExporter` to assert PydanticAI spans flow through.

### Out of scope (other slices own these)
- Skills loader / `list_skills` MCP tool (slice 1, emission side)
- Universal MCP injection (slice 2)
- Prompt caching (slice 3)
- Prompt versioning / `PromptHandle` / replay snapshots (slice 5)
- Grafana dashboard JSON

### Depends-on callouts

| Depends on | What | Minimal interface expected |
|------------|------|-----------------------------|
| Slice 1 (Skills loader) | A stable `skill.activated` structlog event OR a direct callable from the supervisor | `SkillActivation` attrs frozen value with `name: str`, `trigger: str`, `matched_by: str`. This slice calls `record_skill_activation_audit(activation=..., trace_id=..., pipeline_run_id=...)` from the supervisor node. |
| Slice 5 (Prompt versioning) | Once `PromptHandle` lands, `record_agent_call` will be extended with `prompt_version` + `prompt_sha256`. | Caller passes `prompt_handle.version` and `prompt_handle.sha256` into `ExecutionTracer.record_agent_call`. This plan reserves the parameter names but leaves them as `""` until slice 5 lands. |

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Reuse vs new bootstrap module | **Extend `bootstrap_otel.py`** with a sibling `init_traces()` function and a module-level `_traces_initialised` guard | Keeps all OTel provider wiring in one file, shares `Resource` construction with metrics |
| Exporter selection | `LOGFIRE_TOKEN` set → `logfire.configure(send_to_logfire=True, ...)`. Else if `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` set → `TracerProvider` with `BatchSpanProcessor(OTLPSpanExporter(...))`. Else → no-op | Matches "Logfire in dev, Datadog APM in prod"; `LOGFIRE_TOKEN` is the natural dev signal |
| Avoiding double-init | Module-level `_traces_initialised` bool + check `trace.get_tracer_provider().__class__.__name__ != "ProxyTracerProvider"` before `set_tracer_provider` | Logfire installs its own provider; we must not overwrite it |
| Skill activation persistence | **Direct call** to `record_audit_entry` from the supervisor, not log tailing | Explicit, testable, transactional with the same DB connection. Tailing structlog is a side-channel that couples layers the wrong way. |
| Token/cost extraction point | New `_record_agent_result` helper in `domain/pipeline/tracer.py` that takes an `AgentRunResult` and a `node_id` | Centralises extraction, keeps nodes ignorant of tracer schema, extensible for `prompt_version` later |
| Cost calculation | Static per-model rate map in new `domain/evaluation/costing.py`, stored as `token_usage_json.cost_usd` | No infra, no LiteLLM proxy queries on the hot path |
| Node persistence coverage | Wrap `instrumented_node_run(...)` helper so it calls `start_node`/`complete_node` — every node already goes through it, so one edit covers all three graphs | Minimally invasive; guarantees coverage |

## Steps (atomic commits)

- [ ] **Step 1: Settings additions.** Add `logfire_token`, `otel_exporter_otlp_traces_endpoint`, `otel_traces_enabled`, `otel_service_version` to `settings.py`. Unit tests for defaults. Commit: `feat(settings): add OTLP traces exporter env vars`

- [ ] **Step 2: `init_traces` — disabled path.** Add `init_traces()` to `bootstrap_otel.py`. With neither env var set, log `otel.traces.disabled` and return. Unit test patches `logs.log_event`. Commit: `feat(bootstrap): scaffold OTLP traces init with disabled default`

- [ ] **Step 3: `init_traces` — Logfire path.** When `LOGFIRE_TOKEN` set, call `logfire.configure(send_to_logfire=True, token=..., service_name=..., service_version=...)`. Commit: `feat(bootstrap): configure Logfire traces exporter when LOGFIRE_TOKEN set`

- [ ] **Step 4: `init_traces` — OTLP path.** Build `TracerProvider` with `Resource(service.name, service.version, deployment.environment)` + `BatchSpanProcessor(OTLPSpanExporter(endpoint=..., headers=...))`. Commit: `feat(bootstrap): configure OTLP HTTP traces exporter for production`

- [ ] **Step 5: Idempotency + precedence.** Add `_traces_initialised` guard; `LOGFIRE_TOKEN` wins over OTLP; second call is a no-op; does not override existing non-proxy `TracerProvider`. Commit: `feat(bootstrap): guard traces init against double-registration`

- [ ] **Step 6: Wire into `bootstrap.initialise()`.** Call `bootstrap_otel.init_otel()` and `init_traces()` after `_configure_llm_env()` and `configure_logging()`. Update `worker.py` main, Streamlit chat startup, eval runner. Commit: `feat(bootstrap): initialise OTLP metrics and traces from bootstrap.initialise`

- [ ] **Step 7: Tracer agent-result helper.** Add `ExecutionTracer.record_agent_result(*, node_id, agent_name, model_id, result)` that pulls `result.usage()` → `{input_tokens, output_tokens, total_tokens, cost_usd}` and delegates to `record_agent_call`. Commit: `feat(pipeline): extract token/cost from PydanticAI result into tracer`

- [ ] **Step 8: Cost calculation helper.** `domain/evaluation/costing.py::estimate_cost_usd(*, model_id, input_tokens, output_tokens)` with static rate map. Returns `None` for unknown models. Commit: `feat(evaluation): add static LLM cost estimation helper`

- [ ] **Step 9: Node persistence via `instrumented_node_run`.** Accept optional `trace_collector` + `input_data`; call `start_node` on entry, `complete_node` on exit. Update all call-sites in the three graphs. Commit: `feat(graphs): persist node executions via instrumented_node_run helper`

- [ ] **Step 10: Replace `.record(...)` calls with `record_agent_result`.** In all three graphs. Commit: `feat(graphs): persist agent calls with token usage per node`

- [ ] **Step 11: Skill activation audit persistence.** Add `domain/audit/operations.py::record_skill_activation_audit(*, db, activation, trace_id, pipeline_run_id)` wrapping `record_audit_entry` with `action="skill.activated"`. Commit: `feat(audit): persist skill activations to audit log`

- [ ] **Step 12: Supervisor wiring.** In supervisor decision path, when slice 1's skill matcher returns `SkillActivation`, call `record_skill_activation_audit`. Behind `if activations:` with `TODO(slice-1)` marker. Commit: `feat(supervisor): write skill activations to audit log`

- [ ] **Step 13: Integration test with in-memory span exporter.** `tests/integration/test_otel_traces_export.py` uses `InMemorySpanExporter` + `SimpleSpanProcessor`; runs `investigate_alert` against stub Holmes adapter and monkeypatched PydanticAI agent with canned `AgentRunResult` having non-zero `usage()`. Asserts pipeline_runs + node_executions + agent_calls rows + finished spans include `"agent "` prefix. Commit: `test(integration): assert full OTLP trace + persistence flow for SRE investigation`

- [ ] **Step 14: Unit tests for worker & chat startup.** Assert `bootstrap.initialise()` called early. Commit: `test(bootstrap): assert worker and chat initialise traces`

- [ ] **Step 15: PRD checkbox update.** Tick §4 boxes and §7 `bootstrap.initialise()` box. Commit: `docs(prd): mark OTel telemetry exporter acceptance criteria complete`

## Files to Create / Modify

**Modify:**
- `src/sentinel/settings.py`
- `src/sentinel/bootstrap_otel.py`
- `src/sentinel/bootstrap.py`
- `src/sentinel/worker.py`
- `src/sentinel/interfaces/chat/app.py`
- `src/sentinel/evals/runner.py` (or equivalent)
- `src/sentinel/domain/pipeline/tracer.py`
- `src/sentinel/interfaces/graphs/_node_helpers.py`
- `src/sentinel/interfaces/graphs/sre_investigation.py`
- `src/sentinel/interfaces/graphs/support_review.py`
- `src/sentinel/interfaces/graphs/chart_generation.py`
- `src/sentinel/domain/audit/operations.py`
- `src/sentinel/domain/supervisor/quality_gate.py` (or call-site)

**Create:**
- `src/sentinel/domain/evaluation/costing.py`
- `tests/unit/test_bootstrap_otel_traces.py`
- `tests/unit/domain/pipeline/test_tracer_agent_result.py`
- `tests/unit/domain/evaluation/test_costing.py`
- `tests/unit/domain/audit/test_skill_activation_audit.py`
- `tests/unit/interfaces/graphs/test_instrumented_node_run.py`
- `tests/integration/test_otel_traces_export.py`

**New dependencies (pyproject.toml):**
- `logfire` (verify — may already be transitive via PydanticAI)
- `opentelemetry-exporter-otlp-proto-http`
- `opentelemetry-sdk` (already present via metrics slice)

## Test Plan

### Unit
| Test | Asserts |
|------|---------|
| `test_init_traces_disabled_when_no_env_vars` | Logs `otel.traces.disabled`, no provider touched |
| `test_init_traces_uses_logfire_when_token_set` | `logfire.configure` called with expected kwargs |
| `test_init_traces_uses_otlp_when_endpoint_set` | `OTLPSpanExporter` + `TracerProvider` wired |
| `test_init_traces_logfire_takes_precedence_over_otlp` | Both set → Logfire path |
| `test_init_traces_is_idempotent` | Second call no-op |
| `test_init_traces_does_not_override_existing_provider` | Non-proxy provider → no-op |
| `test_record_agent_result_extracts_token_usage` | `persist_agent_call` receives usage dict |
| `test_estimate_cost_usd_known_model` | Correct multiplication |
| `test_estimate_cost_usd_unknown_model_returns_none` | Unknown → None |
| `test_instrumented_node_run_records_start_and_complete` | Fake tracer sees start+complete |
| `test_instrumented_node_run_marks_complete_as_failed_on_exception` | Status = "failed", exception re-raised |
| `test_record_skill_activation_audit_writes_expected_row` | Audit row has action `skill.activated` |

### Integration
- `test_investigate_alert_emits_pipeline_and_node_and_agent_rows` — full pipeline + InMemorySpanExporter
- `test_bootstrap_initialise_wires_logfire_in_localdev`

## Acceptance Criteria Mapping

| PRD line | Covered by |
|----------|------------|
| §4 "PydanticAI spans exported via OTLP — Logfire in dev, Datadog APM in production" | Steps 2–6, integration test Step 13 |
| §4 "per-pipeline-run snapshot" | Step 9 + integration test |
| §4 "token/cost per agent call" | Steps 7–8, 10 + integration test |
| §4 "skill activations persisted to audit log" | Steps 11–12 |
| §7 "`bootstrap.initialise()` configures an OTLP exporter" | Step 6 + unit tests |

## Risks / Open Questions

1. **Datadog OTLP endpoint requires cluster agent in production.** Local dev with no env vars logs `otel.traces.disabled` and returns — nothing breaks. Developers add `LOGFIRE_TOKEN` to `.env` for interactive span inspection.
2. **Logfire provider ownership collision.** Logfire installs its own `TracerProvider`; precedence branch handles this. Verify manually that spans from `agent.run()` reach the Logfire dashboard.
3. **`result.usage()` shape differs across PydanticAI versions.** Use `getattr(usage, "input_tokens", None)`; helper must not crash on `None`.
4. **`instrumented_node_run` signature change touches every graph node.** Risk of breaking chat UI's `TraceCollector.traces` consumer. Mitigation: keep populating `self.traces` inside `record_agent_result`.
5. **Skill activation audit without slice 1.** Land persistence behind empty-list guard with `TODO(slice-1)` marker.
6. **`prompt_version` field stubbed as `""` until slice 5.** Document minimal interface.
7. **Streamlit chat re-runs.** Module-level state survives; verify with manual smoke.
8. **Eval runner entry point** may not exist as a single module — add bootstrap in shared harness if needed.

## Changes
| Date | What changed | Why |
|------|-------------|-----|

## Outcome
_Fill in after completion._
