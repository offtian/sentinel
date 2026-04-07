# Plan: Grafana Metrics Instrumentation

**Status:** in-progress
**Created:** 2026-04-07
**Last updated:** 2026-04-07

## Goal

Add OpenTelemetry-based metrics instrumentation to Sentinel so that Grafana dashboards can
visualise pipeline throughput, latency, confidence scores, LLM call rates, and job queue depth.

## Scope

### In scope
- OTel dependencies and settings (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`)
- `src/sentinel/utils/metrics.py` — cross-cutting recorder helpers (no-op when disabled)
- Meter initialisation wired into application startup
- Instrumentation call-sites in pipeline nodes, approval handlers, and worker jobs

### Out of scope
- Grafana dashboard JSON (managed separately)
- Traces / logs OTel pipelines

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| No-op by default | Instruments start as None | Metrics must never break the app |
| Exception swallowing | _safe_record wraps all calls | Same rationale |
| Module-level singletons | Global _meter / counters | Simple; init_meters() called once at startup |

## Steps

- [x] Task 1: Add OTel dependencies to pyproject.toml
- [x] Task 2: Add metrics settings to settings.py
- [ ] Task 3: Create utils/metrics.py with no-op behaviour and meter setup
- [ ] Task 4: Wire meter initialisation into application startup
- [ ] Task 5: Add instrumentation call-sites

## Changes

| Date | What changed | Why |
|------|-------------|-----|
| 2026-04-07 | Plan file created retroactively | Branch pre-existed plan requirement |

## Outcome

_Fill in after completion._

### What was delivered
- ...

### Follow-up / tech debt
- ...
