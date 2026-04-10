# Plan: Logfire LLM Traces to Tempo

**Status:** in-progress
**Created:** 2026-04-10
**Last updated:** 2026-04-10

## Goal

Export PydanticAI agent OpenTelemetry spans to the existing Tempo instance via the Logfire SDK. All 8 agents already set `instrument=True` but no TracerProvider is configured, so spans are discarded. This wires them up to Tempo for viewing in Grafana.

## Scope

### In scope
- Add `logfire[pydantic]` dependency
- Settings fields for traces enable/endpoint
- `init_traces()` function using Logfire SDK with `send_to_logfire=False`
- Wire into `bootstrap.initialise()`
- Docker-compose, Helm, and .env.default config
- Unit tests

### Out of scope
- Logfire cloud account
- New containers or infrastructure
- Trace sampling/filtering configuration

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Instrumentation SDK | logfire with `send_to_logfire=False` | Enriches PydanticAI spans with agent/model/token metadata, exports to local Tempo via OTLP |
| Import strategy | Deferred import of `logfire` inside `init_traces()` | logfire auto-installs TracerProvider on import; only want that side effect when traces are enabled |
| Export protocol | OTLP HTTP (port 4318) | Already exposed by Tempo in docker-compose |

## Steps

- [ ] Step 1: Add `logfire[pydantic]` to pyproject.toml
- [ ] Step 2: Add `otel_traces_enabled` and `otel_traces_endpoint` settings fields
- [ ] Step 3: Add env vars to docker-compose.yml, helm values-local.yaml, .env.default
- [ ] Step 4: Implement `init_traces()` in bootstrap_otel.py
- [ ] Step 5: Wire `init_traces()` into bootstrap.initialise()
- [ ] Step 6: Add unit tests for init_traces()
- [ ] Step 7: Run tests and lint

## Changes

| Date | What changed | Why |
|------|-------------|-----|

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
