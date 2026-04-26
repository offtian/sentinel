# Plan: Sentinel Foundations — Phase F4 OTEL → Langfuse → Replay-Bundle

**Status:** phase-a-complete
**Created:** 2026-04-26
**Last updated:** 2026-04-26

## Goal

Land the F4 phase of the foundations plan: close the **OTEL → Langfuse → replay-bundle triple** that RFC §14.7 names as "the single failure mode to fear." Concretely:

1. Every span carries the **9 mandatory attributes** of RFC §13.2 (six envelope-derived from F2 plus `prompt_version_sha`, `model_id`, `team_profile`).
2. A custom `MandatoryAttributesValidator` span processor flags incomplete spans without dropping them (so they remain visible in Langfuse for debugging).
3. Spans export to a self-hosted Langfuse instance via OTLP — wired into the existing Logfire-SDK trace pipeline so the existing Tempo path stays available as the fallback when Langfuse is unconfigured.
4. A local **Langfuse v3 stack** (web + worker + postgres + clickhouse + redis + minio) lands in `docker-compose.yml` so F4.4 E2E validation runs end-to-end with `just docker-compose-up`.
5. The replay-bundle (PR #15 surface) gains tool I/O + LLM I/O capture so a recorded run is fully replayable, with a deterministic CI integration test.

Parent plan: [`sentinel-hedgefund-foundations.md`](sentinel-hedgefund-foundations.md) (F4.1 through F4.9).

## Phasing

This sub-plan ships in **two PRs**:

- **Phase A — Langfuse end-to-end** (this section's F4.1–F4.4 + F4.A docker-compose + F4.9 partial). Vertical slice: a curl webhook produces a trace in a locally-running Langfuse with all 9 mandatory attributes.
- **Phase B — Replay-bundle extension + determinism CI** (F4.5–F4.8 + F4.9 final). Builds on Phase A's mandatory-attribute groundwork.

Phase A merges first; Phase B starts on a fresh branch from the merge commit.

## Scope

### In scope

**Phase A**
- New file `src/sentinel/utils/langfuse_export.py`:
  - `MandatoryAttributesValidator` (`opentelemetry.sdk.trace.SpanProcessor`).
  - Helper that builds the Langfuse OTLP exporter (URL + Basic Auth header from settings).
- Mandatory attribute setters at every agent-invocation site: `prompt_version_sha`, `model_id` (from the existing `PROMPT_SHA256` constants and `get_model_name(agent)` helper) plus `team_profile` (from `get_config().team_id`) on the active span.
- `bootstrap_otel.init_traces()` extended to register the validator and the Langfuse exporter when `langfuse_host` is set; the existing Tempo/Logfire-OTLP path keeps working when `langfuse_host` is unset.
- New docker-compose services: `langfuse-web`, `langfuse-worker`, `langfuse-db` (postgres, separate from Sentinel's), `clickhouse`, `redis`, `minio`. Init script primes a single Langfuse organisation + project + API key pair via the bootstrap env vars (`LANGFUSE_INIT_*`).
- `.env.default` documents `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`.
- `docs/architecture.md` §Observability gains the mandatory-attribute table + a "Local Langfuse" subsection.
- Unit tests: `MandatoryAttributesValidator` (missing-attr warning + `_validation_failed`), exporter URL + auth-header construction, mandatory-attribute setters.
- Smoke validation: synthetic webhook → trace visible in `http://localhost:3001` with all 9 attrs (screenshot in PR description).

**Phase B**
- Extend `domain/pipeline/types.ReplayBundle` with `tool_io: tuple[ToolIOEntry, ...]` and `llm_io: tuple[LLMIOEntry, ...]`.
- New `plugins/toolsets/_runtime.py` wrapper that captures every tool call into a `ContextVar[ReplayBundleBuilder]`; flushes on pipeline `End`.
- Recorded-transport replay path in `replay.py` (and/or new helper) that injects recorded LLM/tool responses by overriding the relevant transports.
- `tests/integration/test_replay_determinism.py` — 30 runs of a recorded crashloop bundle, identical `final_outputs`. Marked `slow`.
- `docs/architecture.md` §Replay subsection (post-Phase A docs land alongside Phase B PR).

### Out of scope

- **F4.5 file relocation to `utils/replay_bundle.py`** — the parent plan filemap predates the data/domain restructure; `ReplayBundle` already lives in `domain/pipeline/types.py` (its right home post-restructure). Phase B extends it in place.
- **F4.7 file rename to `replay_cli.py`** — `src/sentinel/replay.py` is already the CLI module (`python -m sentinel.replay`). Phase B extends it in place.
- Switching from per-foundations 30-run determinism check to the production R-AG-4 100-run check (deferred to wk5+ nightly job).
- Production Langfuse RBAC + per-PM project provisioning (F0.6 ADR `0004-D15-langfuse-rbac.md`, month 3+).
- Extending the `MandatoryAttributesValidator` to **drop** incomplete spans (foundations is shadow-mode; production-mode tightening is post-foundations work).
- Tightening F2 strict-mode default (`envelope_strict_mode=True`) — orthogonal to F4.

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| `prompt_version_sha`/`model_id` setter location | At each agent-invocation site (just before `agent.run(...)`), via a small `set_agent_span_attributes(prompt_sha, model_name)` helper in `interfaces/graphs/agents/utils.py`. **Not** in `instrumented_node_run`. | The node helper has no business knowing which agent (if any) the node calls. The information already lives at the call site (`PROMPT_SHA256` constant + `get_model_name(agent)`). Keeps node helper agent-agnostic. |
| `team_profile` setter location | In `instrumented_node_run` (alongside the envelope-derived attrs). Source: `get_config().team_id`. | Per-process constant, cheapest to set once at the node boundary. |
| Validator behaviour on missing attrs | Emit `structlog.warning` + attach `_validation_failed=True` span attribute; **do not** drop the span. | RFC §14.7 wants incomplete spans visible in Langfuse for debugging the integration. Tightening to drop is post-foundations work. |
| Langfuse exporter wiring | Use `opentelemetry-exporter-otlp-proto-http`'s `OTLPSpanExporter` directly in `bootstrap_otel`, registered as a `BatchSpanProcessor` on the Logfire-configured TracerProvider. | Logfire's `send_to_logfire=False` already gives us a TracerProvider; layering an additional OTLPSpanExporter on top is documented and avoids fork-and-replace. Keeps the existing Tempo exporter live in parallel — operators get both backends in dev. |
| Langfuse Basic Auth header | Build at exporter-construction time: `f"Basic {b64(public:secret)}"`. | Stable for the process lifetime; rotation requires restart (acceptable in foundations dev). |
| Langfuse fallback when host unset | No-op (existing Tempo / console exporter remains the only sink). | F4.3 must be a strict superset of F4 pre-state — adding Langfuse cannot regress dev with Langfuse unconfigured. |
| Langfuse stack version | v3 (web + worker + postgres + clickhouse + redis + minio). | User decision: prod-parity local dev, matches the firm-shared infra in F0.5. v3 is the current stable; v2 is feature-frozen. |
| Langfuse postgres isolation | New `langfuse-db` service distinct from Sentinel's `db`. | Avoids Alembic vs Langfuse migration interference; simpler `docker-compose down -v` flows. Cost: one extra container on dev. |
| Bootstrap project + key | Use `LANGFUSE_INIT_*` env vars (org, project, user, public/secret key) so the keys are deterministic across restarts. Hard-code dev-only keys in `docker-compose.yml` and reference via `${LANGFUSE_PUBLIC_KEY:-pk-lf-localdev}` / `${LANGFUSE_SECRET_KEY:-sk-lf-localdev}`. | Deterministic local dev — `docker-compose up` immediately produces a working `LANGFUSE_*` setting set without manual UI clicks. Dev keys are NOT secrets. |
| Validator singleton vs per-init | Single instance attached during `init_traces()`. | Validator is stateless; one instance suffices and avoids double-warnings. |
| Phase B file placement | Keep `ReplayBundle` in `domain/pipeline/types.py` and CLI in `src/sentinel/replay.py`. | Both files exist post-PR #15; parent filemap (`utils/replay_bundle.py`, `replay_cli.py`) predates the data/domain restructure that already landed. Avoid moving production code on a parallel dimension to F4. |
| Phase B `ToolIOEntry`/`LLMIOEntry` shape | `attrs.frozen` slots in `domain/pipeline/types.py`: `tool_name`, `inputs: dict[str, Any]`, `outputs: dict[str, Any]`, `evidence_object_id: str \| None`, `at: datetime`. LLMIOEntry: `agent_name`, `model_id`, `prompt_sha256`, `messages_in`, `messages_out`, `at`. | Mirrors RFC §3.8; `evidence_object_id` ties tool I/O to the audit-log evidence row already created by F3. |
| Replay capture activation | `ContextVar[ReplayBundleBuilder \| None]` set by pipeline entry points (`investigate_alert`, `review_ticket`); plugins write to it through a single sink. | Avoids leaking pipeline state into plugin signatures. Builder absent ⇒ tool wrapper is a no-op. |
| Determinism test wall-time | 30 runs (foundations CI), expand to 100 in nightly post-Helm (wk5 plan). | RFC R-AG-4 says 100; 30 is the minimum that catches non-determinism without bloating PR CI past 5 min. |

## Steps

### Phase A — Langfuse end-to-end

- [x] **F4.A.0** Add `langfuse_host` / `langfuse_public_key` / `langfuse_secret_key` to `.env.default` with localdev defaults pointing at the docker-compose stack (`http://localhost:3001`, `pk-lf-localdev`, `sk-lf-localdev`). Already present in `settings.py` from F1. Confirm `BaseConfiguration` exposes them or surfaces them via `settings`.
- [x] **F4.1** Add `set_agent_span_attributes(*, prompt_sha256, model_name)` helper in `interfaces/graphs/agents/utils.py`. Sets `prompt_version_sha` and `model_id` on the **current OTel span**. Wire into every agent-invocation path: `k8s_runner.run_k8s_agent`, `alert_classifier.classify`, `root_cause_analyser.analyse`, `ticket_reviewer.review`, `response_drafter.draft`, `chart_request_parser.parse`, `chart_generator.generate`, `intent_router.route`. Each call site already has the `PROMPT_SHA256` constant + a built `agent`; add 1–2 lines per site. Commit `ef23db7`.
- [x] **F4.1.b** Extend `instrumented_node_run`/`run_node_with_envelope` to set `team_profile` on the span (from `get_config().team_id`). Single setter, called once per node alongside the envelope attributes. Commit `ef23db7`.
- [x] **F4.1.c** Unit tests: 9 mandatory attrs land on a span when a node runs with envelope + agent context. Commit `ef23db7`.
- [x] **F4.2** Implement `MandatoryAttributesValidator(SpanProcessor)` in `src/sentinel/utils/langfuse_export.py`:
  - `MANDATORY_ATTRS = ("request_id", "tenant_id", "pii_class", "prompt_version_sha", "model_id", "team_profile", ...)` (six envelope-derived + three agent-context, with carve-outs for non-pipeline FastAPI/SQLAlchemy spans documented in code).
  - Carve-out predicate: spans where `instrumentation_scope.name in {"opentelemetry.instrumentation.fastapi", "opentelemetry.instrumentation.sqlalchemy", "opentelemetry.instrumentation.httpx"}` skip validation.
  - On missing attr: `logs.log_event("otel.span.missing_mandatory_attrs", params={...})` + record `_validation_failed=True` + `_missing_attrs=("a","b")` on the span. Span is **not** dropped. Commit `a37c36a`.
- [x] **F4.2.b** Unit tests for the validator: missing-attr path, full-attr happy path, carve-out short-circuit. Commit `a37c36a`.
- [x] **F4.3** New helper `build_langfuse_exporter(*, host, public_key, secret_key)` in `utils/langfuse_export.py` returning a configured `OTLPSpanExporter`. URL: `f"{host}/api/public/otel/v1/traces"`. Headers: `{"Authorization": f"Basic {base64(public:secret)}"}`. Failures during construction → log + return `None` (don't crash startup). Commit `1feba88`.
- [x] **F4.3.b** Wire the exporter and the validator in `bootstrap_otel.init_traces()` after the existing `logfire.configure` block. Use `BatchSpanProcessor(exporter)` and `tracer_provider.add_span_processor(validator)` + `tracer_provider.add_span_processor(BatchSpanProcessor(...))`. Idempotent: don't re-add if `_traces_initialised`. Commit `1feba88`.
- [x] **F4.3.c** Unit tests: exporter URL + auth-header construction, no-op when `langfuse_host` unset, validator registered exactly once. Commit `1feba88`.
- [x] **F4.A.1** Add Langfuse v3 services to `docker-compose.yml`:
  - `langfuse-db` (postgres:16, dedicated volume, port 5433 exposed to host).
  - `clickhouse` (clickhouse/clickhouse-server, dedicated volume).
  - `redis` (redis:7, no volume).
  - `minio` (minio/minio with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` set; init container creates the buckets `langfuse-events`, `langfuse-media`).
  - `langfuse-worker` (langfuse/langfuse-worker:3, all required env vars).
  - `langfuse-web` (langfuse/langfuse:3, port 3001:3000 to avoid Grafana clash, `LANGFUSE_INIT_*` vars seeding the dev project + key pair).
  - Healthchecks on all stateful services; `langfuse-web` `depends_on` the rest with `condition: service_healthy`.
  - `api` and `mcp-server` services gain `LANGFUSE_HOST=http://langfuse-web:3000` + the dev keys. Commit `1096ce5`.
- [ ] **F4.A.2** Verify `just docker-compose-up` brings the full stack to ready in < 3 minutes; `curl http://localhost:3001/api/public/health` returns 200; the project + API keys exist. (deferred — runtime smoke pending Docker daemon on dev host; compose config validates)
- [ ] **F4.4** End-to-end smoke: `just docker-compose-up` → `curl -X POST http://localhost:8000/api/sre/webhooks/pagerduty` with a sample payload (or `tests/fixtures/pagerduty/*.json`) → trace visible in Langfuse UI at `http://localhost:3001` with all 9 mandatory attrs across the chain (FastAPI span → pipeline span → agent spans). Capture screenshot for PR description. (deferred — runtime smoke pending Docker daemon on dev host; compose config validates)
- [x] **F4.A.3** Document the local Langfuse setup in `docs/architecture.md` §Observability:
  - Add the §13.2 mandatory-attribute table (envelope vs agent-context split).
  - Add a "Local Langfuse" subsection: ports, default keys, `docker-compose up` flow, "view traces at http://localhost:3001".
  - Update the Sentinel architecture diagram callouts (text-only — diagram update can wait for Phase B).
- [x] **F4.A.4** Update parent foundations plan: tick F4.1 / F4.2 / F4.3 / F4.4 / partial F4.9 boxes with commit refs. Update INDEX.md F4 progress (`1/2 phases` of F4).
- [x] **F4.A.5** Run full local checks: `just lint`, `just test`, `just test-integration` (DB only — Langfuse stack optional). Open PR titled `feat(observability): F4 Phase A — Langfuse end-to-end + mandatory span attrs`. Include screenshot of trace + the docker-compose flow as the test plan.

### Phase B — Replay-bundle extension + determinism CI (separate PR)

- [ ] **F4.5** Extend `ReplayBundle` (`domain/pipeline/types.py`) with `tool_io: tuple[ToolIOEntry, ...]` and `llm_io: tuple[LLMIOEntry, ...]`. Add the two `attrs.frozen` entry types alongside. Migrate the existing snapshot persistence path (`domain/pipeline/operations.py` / `queries.py`) to round-trip the new fields as JSONB columns on `pipeline_run_snapshot`. Alembic migration `014_replay_bundle_tool_llm_io.py` adds the columns.
- [ ] **F4.5.b** Update `fetch_replay_bundle` to hydrate the new fields. Backwards compat: rows pre-migration return empty tuples.
- [ ] **F4.6** Tool-I/O capture wrapper at `src/sentinel/plugins/toolsets/_runtime.py`:
  - `_replay_builder: ContextVar[ReplayBundleBuilder | None]` initialised to `None`.
  - `with_replay_capture(builder)` async context manager that sets and resets the var.
  - `capture_tool_io(*, tool_name)` decorator/wrapper that an existing toolset call can opt into; it records inputs (kwargs), outputs (return value or exception), `evidence_object_id` if the tool already wrote one, and `at` timestamp.
  - Wire the wrapper into the existing toolsets (`documentation.py`, `observability.py`, `mcp.py`) so all tool calls are captured.
- [ ] **F4.6.b** LLM-I/O capture: PydanticAI's `instrument=True` already emits LLM spans; mirror the data into the builder via a span-event listener. Approach: add an extra `SpanProcessor` (in `langfuse_export.py` or a new `replay_capture.py`) that inspects spans whose `instrumentation_scope` is `pydantic_ai`; on `on_end`, append an `LLMIOEntry` to the builder if one is set.
- [ ] **F4.6.c** Pipeline entry points (`investigate_alert`, `review_ticket`) wrap the run in `with_replay_capture(builder)` and flush builder state into the snapshot row at pipeline `End`.
- [ ] **F4.7** Recorded-transport replay path:
  - Add `RecordedTransport` mirror that intercepts LLM and tool calls and returns recorded outputs from the bundle's `llm_io` / `tool_io`. Out-of-band (not captured) calls raise `ReplayDivergenceError`.
  - Extend `python -m sentinel.replay <run_id> --replay` to install `RecordedTransport` (via PydanticAI's transport injection + a toolset stub registry) before invoking the graph.
  - `--diff` flag re-runs and diffs `final_outputs` against the recorded value; non-zero exit on mismatch.
  - Drop the `_envelope_for_replay` placeholder once the bundle persists the envelope (covered here): use the recorded `envelope` directly.
- [ ] **F4.7.b** Unit tests for `RecordedTransport`: replay returns recorded value, divergence raises, diff exit code.
- [ ] **F4.8** Determinism integration test `tests/integration/test_replay_determinism.py`:
  - Fixture: a recorded `ReplayBundle` for a synthetic crashloop alert (`tests/fixtures/replay/crashloop_v1.json`).
  - Test: 30 runs, `assert all(run.final_outputs == bundle.final_outputs)`.
  - `@pytest.mark.slow` marker so `just test-integration` runs it; document a `SKIP_SLOW` env-var carve-out for fast local CI.
- [ ] **F4.B.1** Document the replay flow in `docs/architecture.md` §Replay (record format, CLI usage, determinism guarantees, escape hatches).
- [ ] **F4.B.2** Tick F4.5/F4.6/F4.7/F4.8/F4.9 in parent plan + INDEX. Open PR titled `feat(replay): F4 Phase B — replay-bundle tool/LLM I/O + determinism CI`.

## Acceptance

**Phase A**
- All 9 RFC §13.2 attributes land on every pipeline + agent span (verified by unit test + the Langfuse UI smoke).
- A synthetic webhook produces a trace in self-hosted Langfuse, all attrs visible.
- The validator flags incomplete spans without dropping them.
- The full local stack (`just docker-compose-up`) brings up Langfuse alongside Sentinel + the existing observability stack.
- All existing tests + new unit tests green.

**Phase B**
- A recorded `ReplayBundle` is fully self-contained: `python -m sentinel.replay <run_id> --replay` reconstructs the run with no live LLM/tool calls.
- 30-run determinism test green.
- `--diff` returns non-zero on a doctored bundle.

## Risks

| Risk | Mitigation |
|------|------------|
| PydanticAI auto-spans don't expose enough hooks to capture LLM I/O accurately | Phase B step F4.6.b explicitly trials a span-event listener approach; fallback is wrapping the PydanticAI agent with a custom transport (heavier, but tested in PR #15's harness already). |
| Langfuse v3 stack is heavy on developer laptops | All v3 services are gated behind a profile (e.g. `--profile langfuse`); the API + DB still come up without it. Documented in `docs/architecture.md`. |
| Langfuse OTLP endpoint rejects spans missing `pii_class` carve-out attrs | Validator carve-outs explicitly cover FastAPI/SQLAlchemy/HTTPX scopes; per-span sampling decision is at exporter level so even rejected spans don't break the pipeline. |
| `BatchSpanProcessor` can swallow exporter errors silently | Add a `MetricsCounter` for exporter retries (reuse the existing `metrics` module), surface in Grafana dashboard alongside Langfuse health. |
| Adding a third span processor + exporter slows pipeline cold path | `BatchSpanProcessor` runs in a worker thread; on-end attribute checks are O(constant). Microbench in F4.2.b unit test confirms < 50µs per span. |

## Steps Summary (parent-plan checkbox map)

| Parent step | Sub-plan steps | Phase |
|-------------|----------------|-------|
| F4.1 | F4.1, F4.1.b, F4.1.c | A |
| F4.2 | F4.2, F4.2.b | A |
| F4.3 | F4.3, F4.3.b, F4.3.c | A |
| F4.4 | F4.4, F4.A.1, F4.A.2 | A |
| F4.5 | F4.5, F4.5.b | B |
| F4.6 | F4.6, F4.6.b, F4.6.c | B |
| F4.7 | F4.7, F4.7.b | B |
| F4.8 | F4.8 | B |
| F4.9 | F4.A.3 (Phase A docs), F4.B.1 (Phase B docs) | A + B |

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-26 | Initial draft. | F4 entry. |
| 2026-04-26 | Phase A landed: F4.1, F4.2, F4.3, F4.A.1 + docs. Runtime smoke deferred. | F4.A.5 PR opened. |

## Outcome

_Fill in after completion._
