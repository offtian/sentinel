# Grafana Dashboards & Metrics Instrumentation — Design

**Date:** 2026-04-07
**Status:** Approved (pending implementation plan)
**Related PRD section:** §4 Observability & Feedback Loop

## Goal

Expose Sentinel's runtime health and business effectiveness as Prometheus metrics and visualise them in a hierarchical set of Grafana dashboards. The docker-compose stack already runs Prometheus, Loki, Tempo, and Grafana, but the application currently exposes no `/metrics` endpoint and ships no dashboards.

## Scope

**In scope (v1):**
- OpenTelemetry-based metrics SDK wired into both API and worker processes
- `/metrics` Prometheus exposition endpoint on the API (port 8000) and worker (port 8001)
- ~9 custom business metrics + auto-instrumented HTTP/runtime metrics
- 4 Grafana dashboards (1 overview + 3 detail) provisioned from JSON in the repo
- Helm chart updates for Prometheus scraping
- Tests for instrumentation, the `/metrics` endpoint, and dashboard schema validity

**Out of scope (deferred):**
- OpenTelemetry **traces** to Tempo — the OTel SDK setup is forward-compatible but tracing instrumentation comes later
- Datadog APM (`ddtrace`) — superseded by OTel
- Terraform-managed dashboards (see "Future Work")
- Per-vendor adapter latency metrics (Slack, PagerDuty, Jira, Datadog/Grafana clients)
- Circuit breaker state as metrics
- Token cost counters
- Grafana alert rules

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Metrics library | OpenTelemetry SDK + Prometheus exporter | Future-proof: same SDK enables Tempo tracing later. Auto-instrumentation for FastAPI/SQLAlchemy/httpx removes hand-written HTTP metrics. |
| Dashboard provisioning | JSON files in repo, auto-loaded via existing Grafana provisioning | Zero-friction local dev; reproducible; tiny on-disk footprint (~200KB total); fits the existing infra-as-code pattern. Terraform considered but rejected for v1. |
| Code organisation | Cross-cutting `utils/metrics.py` module + thin recorder helpers | Mirrors how `structlog` is used today — metrics are inherently a cross-cutting observability concern; protocol-based indirection would be over-engineering. `utils` is the lowest layer in the import-linter hierarchy so all layers can import it. |
| Dashboard hierarchy | Overview → drill-down (SRE / Support / Worker) | One landing page for "is everything OK?" with click-through to subsystem detail. |
| Dashboard scope | Combined operational + business | Single source of truth for both "is Sentinel healthy?" and "is Sentinel effective?". |
| Metric scope | "Standard" (~14 metrics) | Sweet spot — meaningful coverage without instrumenting every code path. |
| Worker metrics endpoint | Separate port (8001) on the worker process | Worker has no FastAPI app; small Starlette/aiohttp app exposes `/metrics`. Prometheus scrapes API and worker as separate targets. |
| Failure mode | Metrics never block the app | `init_otel()` and every `metrics.record_*()` call is wrapped in try/except. `OTEL_METRICS_ENABLED=false` makes everything a no-op. |

## Architecture

### New modules

```
src/sentinel/
├── utils/
│   └── metrics.py              # OTel meter setup + named recorder helpers
├── bootstrap/
│   └── otel.py                 # init_otel() — configures MeterProvider + Prometheus exporter
└── interfaces/api/
    └── app.py                  # mount /metrics route + auto-instrumentation
```

### Component responsibilities

**`bootstrap/otel.py`**
- Single `init_otel()` function called from API lifespan and worker startup
- Creates an OTel `MeterProvider` with a `PrometheusMetricReader`
- Auto-instruments FastAPI, SQLAlchemy, httpx via OTel instrumentation packages
- Adds runtime instrumentation (CPU, memory, GC)
- Wrapped in try/except — failures log via structlog and continue
- No-op when `OTEL_METRICS_ENABLED=false`

**`utils/metrics.py`**
- Module-level singleton meters/counters/histograms/gauges
- Named recorder helper functions:
  - `record_pipeline_node_duration(*, pipeline, node, duration_seconds, status)`
  - `record_investigation_completed(*, confidence_label, approval_required, outcome)`
  - `record_review_completed(*, confidence_label, outcome)`
  - `record_approval_decision(*, decision, pipeline)`
  - `record_confidence_score(*, pipeline, score)`
  - `record_llm_call(*, agent, model, duration_seconds, status)`
  - `record_job_processed(*, job_type, outcome, duration_seconds)`
  - `set_job_queue_depth(*, job_type, status, depth)`
- Each helper catches exceptions internally and logs once at WARN — never raises
- Helpers no-op when OTel is disabled

**`/metrics` endpoint (API)**
- `prometheus_client.make_asgi_app()` mounted on the FastAPI app at `/metrics`
- The OTel `PrometheusMetricReader` writes into the global Prometheus registry which the ASGI app serves

**Worker `/metrics` endpoint**
- Tiny Starlette app run alongside the worker, exposing `/metrics` on port 8001
- Same OTel setup as the API; reuses `bootstrap/otel.py`
- Lifecycle bound to the worker process — starts in worker startup, stops on SIGTERM

## Metric Catalogue

Naming follows Prometheus conventions: `_total` for counters, `_seconds` for durations, snake_case throughout.

### HTTP layer (auto-instrumented)
| Metric | Type | Labels |
|---|---|---|
| `http_server_request_duration_seconds` | histogram | method, route, status_code |
| `http_server_active_requests` | gauge | — |

### SRE pipeline (custom)
| Metric | Type | Labels |
|---|---|---|
| `sentinel_investigations_total` | counter | confidence_label, approval_required, outcome |
| `sentinel_pipeline_node_duration_seconds` | histogram | pipeline, node, status |
| `sentinel_confidence_score` | histogram (buckets 0.0–1.0 step 0.1) | pipeline |
| `sentinel_approval_decisions_total` | counter | decision (approve/reject/auto_approve), pipeline |

### Support pipeline (custom)
| Metric | Type | Labels |
|---|---|---|
| `sentinel_reviews_total` | counter | confidence_label, outcome |

(Reuses `sentinel_pipeline_node_duration_seconds` and `sentinel_confidence_score` with `pipeline=support`.)

### LLM calls
| Metric | Type | Labels |
|---|---|---|
| `sentinel_llm_calls_total` | counter | agent, model, status |
| `sentinel_llm_call_duration_seconds` | histogram | agent, model |

### Job queue & worker
| Metric | Type | Labels |
|---|---|---|
| `sentinel_job_queue_depth` | gauge | job_type, status |
| `sentinel_jobs_processed_total` | counter | job_type, outcome |
| `sentinel_job_duration_seconds` | histogram | job_type |

### Process info (auto-instrumented)
- `process_cpu_seconds_total`
- `process_resident_memory_bytes`
- `python_gc_*`

**Total: 9 custom + ~5 auto-instrumented = ~14 metrics.**

### Instrumentation points

| Metric | Where instrumented |
|---|---|
| `sentinel_pipeline_node_duration_seconds` | Decorator/helper in `interfaces/graphs/_node_helpers.py` so we instrument once for all nodes |
| `sentinel_investigations_total` / `sentinel_reviews_total` | Final node of each pipeline (or in supervisor on completion) |
| `sentinel_confidence_score` | `DetermineConfidence` node |
| `sentinel_approval_decisions_total` | Approval API endpoint handlers |
| `sentinel_llm_calls_total` / `_duration_seconds` | LiteLLM client call site (single chokepoint) |
| `sentinel_job_queue_depth` | Periodic background task in worker (every 15s, queries DB) |
| `sentinel_jobs_processed_total` / `_duration_seconds` | Worker job execution loop |

## Dashboards

All four dashboards live in a "Sentinel" folder in Grafana, provisioned from `docker/grafana/provisioning/dashboards/sentinel/`. Dashboard config registered via `docker/grafana/provisioning/dashboards/sentinel.yaml`. Default time range: last 1 hour, refresh 30s. All queries use the existing `prometheus` datasource UID.

### 1. `sentinel-overview.json` — top level
**Purpose:** "Is everything healthy and effective right now?"

- **Row 1 — Health:** API request rate, API p95 latency, API error rate %, worker job success rate
- **Row 2 — Throughput (24h):** investigations completed, reviews completed, approvals pending count
- **Row 3 — Quality:** avg confidence (SRE), avg confidence (Support), approval rate %, auto-approval rate %
- **Row 4 — Drill-down:** clickable panels linking to SRE / Support / Worker dashboards

### 2. `sentinel-sre.json` — SRE pipeline detail
- Investigation rate by `confidence_label` (stacked timeseries)
- Pipeline node duration heatmap (per node, p50/p95/p99)
- Confidence score distribution histogram
- Approval decisions stacked bar (approve/reject/auto_approve)
- LLM calls by agent (alert_classifier, root_cause) — count + p95 latency
- Failure rate by node (table)

### 3. `sentinel-support.json` — Support pipeline detail
- Review rate by `confidence_label`
- Pipeline node duration heatmap (classify_ticket / search_documentation / draft_response / determine_confidence)
- Confidence score distribution
- LLM calls by agent (ticket_reviewer, response_drafter)

### 4. `sentinel-worker.json` — worker & queue detail
- Job queue depth by `job_type` and `status` (stacked area)
- Job processing rate by outcome
- Job duration p50/p95/p99 by type
- Worker process CPU & memory (auto-instrumented runtime metrics)

## Error Handling

- `init_otel()` is wrapped in try/except — failure logs via structlog and continues. Sentinel must never fail to start because metrics broke.
- Each `metrics.record_*()` helper catches and swallows exceptions internally (logs once at WARN). Metrics recording must never break a pipeline node.
- `OTEL_METRICS_ENABLED` env var (default `true`) — when `false`, all helpers become no-ops, mirroring the `is_configured` pattern of vendor adapters.
- The `/metrics` endpoint returns an empty registry response (not 500) if OTel is uninitialised.

## Testing

### Unit
- `tests/unit/utils/test_metrics.py`
  - Each helper increments the correct meter with the correct labels (use OTel's `InMemoryMetricReader` for assertions, not the Prometheus exporter)
  - Helpers no-op cleanly when `OTEL_METRICS_ENABLED=false`
  - Exceptions raised inside helpers are swallowed

### Integration
- `tests/integration/interfaces/api/test_metrics_endpoint.py`
  - Hit `/metrics` on a real FastAPI test client
  - Assert valid Prometheus exposition format
  - Assert expected custom metric names appear
- One end-to-end test runs a fake SRE investigation through the pipeline and asserts `sentinel_investigations_total` increments

### Dashboard validation
- `tests/integration/test_grafana_dashboards.py`
  - Loads each provisioned dashboard JSON
  - Validates against the Grafana dashboard schema
  - Asserts every PromQL query references metric names that exist in `utils/metrics.py` (catches metric/dashboard drift)

## Configuration

New env vars in `.env.default`:

```
OTEL_METRICS_ENABLED=true
OTEL_SERVICE_NAME=sentinel
WORKER_METRICS_PORT=8001
```

## Rollout / Implementation Sequence

1. Add `opentelemetry-*` and `prometheus-client` deps to `pyproject.toml`
2. Build `utils/metrics.py` with helpers + tests (no callers yet)
3. Build `bootstrap/otel.py`; wire into API lifespan
4. Mount `/metrics` endpoint on the API
5. Instrument pipeline nodes via a single decorator in `_node_helpers.py`
6. Instrument the LLM call site
7. Instrument approval API endpoints + worker job loop
8. Add worker `/metrics` server on port 8001
9. Write 4 Grafana dashboard JSONs + provisioning config
10. Update `docker/prometheus/prometheus.yml` to scrape worker port 8001
11. Add Helm chart Prometheus annotations + worker metrics service
12. Update PRD §4: check off new acceptance criteria; mark Datadog APM gap as superseded by OTel; add Terraform-managed dashboards as a future-work item

## Documentation Updates

- `docs/prd.md` — check off new metrics acceptance criteria; mark Datadog APM gap as superseded by OTel; add Terraform-managed dashboards under "Future Work"
- `docs/architecture.md` — add a short observability section describing the metrics flow
- `docs/plans/grafana-metrics.md` — implementation plan, generated by writing-plans skill from this design

## Future Work

- **Terraform-managed Grafana dashboards.** When Sentinel is deployed against multiple shared Grafana instances (e.g., a prod Grafana shared with other services), or when dashboard RBAC / folder management as code becomes useful, migrate from JSON provisioning to the Grafana Terraform provider. The dashboard JSON itself will still live in the repo — Terraform just manages how it gets pushed. Triggers for revisiting: (a) we add a second consumer of the prod Grafana, (b) we need org-level RBAC, or (c) drift between local-dev and prod dashboards becomes a recurring problem.
- **OpenTelemetry tracing → Tempo.** The OTel SDK is already initialised; adding tracing means adding a `TracerProvider` with an OTLP span exporter pointed at Tempo, and decorating pipeline nodes / LLM calls with spans. This delivers the §4 "Datadog APM distributed tracing" PRD gap via OTel + Tempo instead of `ddtrace` + Datadog.
- **Per-vendor adapter latency metrics** (Slack, PagerDuty, Jira, Datadog/Grafana clients) — adds visibility into external dependency health.
- **Circuit breaker state as metric** — gauge with state label per protected client.
- **Token cost counter** — `sentinel_llm_tokens_total{agent, model, kind=prompt|completion}` summed via LiteLLM response metadata.
- **Grafana alert rules** — provisioned alerting on key thresholds (error rate, queue depth, p95 latency).
