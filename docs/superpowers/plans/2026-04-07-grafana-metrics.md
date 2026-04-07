# Grafana Dashboards & Metrics Instrumentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Sentinel runtime health and business effectiveness as Prometheus metrics via OpenTelemetry, and visualise them in a hierarchical set of Grafana dashboards (overview → SRE / Support / Worker drill-downs).

**Architecture:** OpenTelemetry SDK with a Prometheus exporter writes into the global `prometheus_client` registry. The API exposes `/metrics` via a mounted ASGI app; the worker exposes `/metrics` on a separate Starlette app on port 8001. A cross-cutting `utils/metrics.py` module provides typed recorder helpers (counters / histograms / gauges) that all layers can call. Dashboards are JSON files auto-loaded by Grafana via the existing `docker/grafana/provisioning/` mechanism.

**Tech Stack:** OpenTelemetry SDK (`opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-prometheus`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlalchemy`, `opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-system-metrics`), `prometheus-client`, `starlette` (already a FastAPI dep), Grafana provisioning JSON.

**Spec:** [docs/superpowers/specs/2026-04-07-grafana-metrics-design.md](../specs/2026-04-07-grafana-metrics-design.md)

---

## File Structure Overview

**Created:**
- `src/sentinel/utils/metrics.py` — OTel meter setup, lazy meter accessor, typed recorder helpers
- `src/sentinel/bootstrap_otel.py` — `init_otel()` and `shutdown_otel()` (sibling to existing `bootstrap.py`, since `bootstrap` is a single module not a package)
- `src/sentinel/interfaces/graphs/_node_helpers.py` — `instrumented_node_run()` decorator
- `src/sentinel/worker_metrics.py` — tiny Starlette app exposing `/metrics` on the worker
- `tests/unit/utils/__init__.py`
- `tests/unit/utils/test_metrics.py`
- `tests/unit/test_bootstrap_otel.py`
- `tests/integration/interfaces/api/test_metrics_endpoint.py`
- `tests/integration/test_grafana_dashboards.py`
- `docker/grafana/provisioning/dashboards/sentinel.yaml` — provisioning config
- `docker/grafana/provisioning/dashboards/sentinel/sentinel-overview.json`
- `docker/grafana/provisioning/dashboards/sentinel/sentinel-sre.json`
- `docker/grafana/provisioning/dashboards/sentinel/sentinel-support.json`
- `docker/grafana/provisioning/dashboards/sentinel/sentinel-worker.json`

**Modified:**
- `pyproject.toml` — add OTel + prometheus-client deps
- `src/sentinel/settings.py` — add `otel_metrics_enabled`, `otel_service_name`, `worker_metrics_port`
- `src/sentinel/interfaces/api/app.py` — call `init_otel()` in lifespan, mount `/metrics`
- `src/sentinel/worker.py` — call `init_otel()`, start `worker_metrics` server, instrument job loop
- `src/sentinel/interfaces/graphs/sre_investigation.py` — use `instrumented_node_run` on nodes; record `sentinel_investigations_total` and `sentinel_confidence_score`
- `src/sentinel/interfaces/graphs/support_review.py` — same instrumentation
- `src/sentinel/interfaces/graphs/agents/utils.py` — wrap LiteLLM call site to record LLM metrics
- `src/sentinel/interfaces/api/routers/sre/router.py` — record `sentinel_approval_decisions_total` in `/approve` and `/reject` handlers
- `docker-compose.yml` — expose worker metrics port (if worker runs in compose)
- `docker/prometheus/prometheus.yml` — add scrape job for worker
- `helm/sentinel/templates/service.yaml` — add Prometheus annotations + worker metrics service
- `helm/sentinel/templates/deployment.yaml` — annotate worker pod with metrics port
- `.env.default` — add new env vars
- `docs/prd.md` — check off acceptance criteria; mark Datadog APM gap as superseded
- `docs/architecture.md` — add observability section

---

## Phase 1: Dependencies & Settings

### Task 1: Add OpenTelemetry and prometheus-client dependencies

**Files:**
- Modify: `pyproject.toml` (dependencies block, lines 29-60)

- [ ] **Step 1: Add deps to pyproject.toml**

Add these lines to the `dependencies` array in `[project]` (after the existing entries, before the closing `]`):

```toml
    "opentelemetry-api>=1.27",
    "opentelemetry-sdk>=1.27",
    "opentelemetry-exporter-prometheus>=0.48b0",
    "opentelemetry-instrumentation-fastapi>=0.48b0",
    "opentelemetry-instrumentation-sqlalchemy>=0.48b0",
    "opentelemetry-instrumentation-httpx>=0.48b0",
    "opentelemetry-instrumentation-system-metrics>=0.48b0",
    "prometheus-client>=0.21",
```

- [ ] **Step 2: Re-lock and install**

```bash
just lock
just install
```

Expected: `uv.lock` updated, packages installed without conflict.

- [ ] **Step 3: Verify imports work**

```bash
.venv/bin/python -c "from opentelemetry import metrics; from opentelemetry.exporter.prometheus import PrometheusMetricReader; from prometheus_client import make_asgi_app; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add OpenTelemetry and prometheus-client dependencies"
```

---

### Task 2: Add metrics settings

**Files:**
- Modify: `src/sentinel/settings.py`
- Modify: `.env.default`

- [ ] **Step 1: Read current settings.py to find the right Settings class to modify**

```bash
grep -n "class.*Settings" src/sentinel/settings.py
```

Expected: shows the top-level `Settings` class (likely a `pydantic_settings.BaseSettings` subclass).

- [ ] **Step 2: Add settings fields**

Find the top-level `Settings` class in `src/sentinel/settings.py` and add these fields (placed alongside other observability-related fields, or at the end of the class body):

```python
    otel_metrics_enabled: bool = True
    otel_service_name: str = "sentinel"
    worker_metrics_port: int = 8001
```

- [ ] **Step 3: Add to .env.default**

Append to `.env.default`:

```
# OpenTelemetry / Metrics
OTEL_METRICS_ENABLED=true
OTEL_SERVICE_NAME=sentinel
WORKER_METRICS_PORT=8001
```

- [ ] **Step 4: Verify settings load**

```bash
.venv/bin/python -c "from sentinel.settings import get_settings; s = get_settings(); print(s.otel_metrics_enabled, s.otel_service_name, s.worker_metrics_port)"
```

Expected: `True sentinel 8001`

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/settings.py .env.default
git commit -m "feat(metrics): add OpenTelemetry settings"
```

---

## Phase 2: Metrics Module

### Task 3: Create utils/metrics.py with no-op behaviour and meter setup

**Files:**
- Create: `src/sentinel/utils/metrics.py`
- Create: `tests/unit/utils/__init__.py`
- Create: `tests/unit/utils/test_metrics.py`

- [ ] **Step 1: Create the test directory init file**

```bash
touch tests/unit/utils/__init__.py
```

- [ ] **Step 2: Write the failing test for no-op behaviour**

Create `tests/unit/utils/test_metrics.py`:

```python
from __future__ import annotations

from unittest import mock

from sentinel.utils import metrics


class TestRecordInvestigationCompleted:
    def test_does_not_raise_when_metrics_disabled(self):
        # Given metrics are disabled
        with mock.patch.object(metrics, "_meter", None):
            # When recording an investigation
            # Then no exception is raised
            metrics.record_investigation_completed(
                confidence_label="high",
                approval_required=False,
                outcome="completed",
            )

    def test_swallows_exceptions_during_recording(self):
        # Given a meter that raises on use
        broken_counter = mock.Mock()
        broken_counter.add.side_effect = RuntimeError("boom")
        with mock.patch.object(metrics, "_investigations_total", broken_counter):
            # When recording — Then no exception escapes
            metrics.record_investigation_completed(
                confidence_label="high",
                approval_required=False,
                outcome="completed",
            )
```

- [ ] **Step 3: Run the test to confirm it fails**

```bash
just test tests/unit/utils/test_metrics.py -v
```

Expected: ImportError or AttributeError (`metrics` module / function does not exist).

- [ ] **Step 4: Create utils/metrics.py with minimal no-op skeleton**

Create `src/sentinel/utils/metrics.py`:

```python
"""

Cross-cutting metrics recording helpers backed by OpenTelemetry.

All recorder functions swallow exceptions and no-op when metrics are
disabled — metrics must never break the application.
"""

from __future__ import annotations

from typing import Any

from sentinel.utils import logs

# Module-level singletons populated by `init_meters()`. They start as None so
# helpers no-op cleanly until OTel has been initialised.
_meter: Any | None = None
_investigations_total: Any | None = None
_reviews_total: Any | None = None
_pipeline_node_duration: Any | None = None
_confidence_score: Any | None = None
_approval_decisions_total: Any | None = None
_llm_calls_total: Any | None = None
_llm_call_duration: Any | None = None
_jobs_processed_total: Any | None = None
_job_duration: Any | None = None
_job_queue_depth: Any | None = None

_warned_once = False


def _safe_record(name: str, fn: Any) -> None:
    """

    Run a recording callable and swallow any exception, logging once at WARN.
    """
    global _warned_once
    try:
        fn()
    except Exception as exc:
        if not _warned_once:
            logs.log_exception(exc, params={"recorder": name})
            _warned_once = True


def init_meters(*, meter: Any) -> None:
    """

    Populate module-level instrument singletons from an OTel meter.
    """
    global _meter
    global _investigations_total, _reviews_total
    global _pipeline_node_duration, _confidence_score
    global _approval_decisions_total
    global _llm_calls_total, _llm_call_duration
    global _jobs_processed_total, _job_duration, _job_queue_depth

    _meter = meter

    _investigations_total = meter.create_counter(
        "sentinel_investigations_total",
        description="Total SRE investigations completed",
    )
    _reviews_total = meter.create_counter(
        "sentinel_reviews_total",
        description="Total support reviews completed",
    )
    _pipeline_node_duration = meter.create_histogram(
        "sentinel_pipeline_node_duration_seconds",
        unit="s",
        description="Duration of pipeline node execution",
    )
    _confidence_score = meter.create_histogram(
        "sentinel_confidence_score",
        description="Confidence score recorded by DetermineConfidence nodes",
    )
    _approval_decisions_total = meter.create_counter(
        "sentinel_approval_decisions_total",
        description="Approval decisions recorded by reviewers or auto-approval",
    )
    _llm_calls_total = meter.create_counter(
        "sentinel_llm_calls_total",
        description="LLM calls dispatched via the LiteLLM gateway",
    )
    _llm_call_duration = meter.create_histogram(
        "sentinel_llm_call_duration_seconds",
        unit="s",
        description="Duration of LLM calls dispatched via the LiteLLM gateway",
    )
    _jobs_processed_total = meter.create_counter(
        "sentinel_jobs_processed_total",
        description="Worker jobs processed",
    )
    _job_duration = meter.create_histogram(
        "sentinel_job_duration_seconds",
        unit="s",
        description="Worker job duration",
    )
    _job_queue_depth = meter.create_gauge(
        "sentinel_job_queue_depth",
        description="Current job queue depth by job type and status",
    )


def reset_meters() -> None:
    """

    Reset all instrument singletons. Used by tests.
    """
    global _meter
    global _investigations_total, _reviews_total
    global _pipeline_node_duration, _confidence_score
    global _approval_decisions_total
    global _llm_calls_total, _llm_call_duration
    global _jobs_processed_total, _job_duration, _job_queue_depth
    global _warned_once

    _meter = None
    _investigations_total = None
    _reviews_total = None
    _pipeline_node_duration = None
    _confidence_score = None
    _approval_decisions_total = None
    _llm_calls_total = None
    _llm_call_duration = None
    _jobs_processed_total = None
    _job_duration = None
    _job_queue_depth = None
    _warned_once = False


def record_investigation_completed(
    *,
    confidence_label: str,
    approval_required: bool,
    outcome: str,
) -> None:
    """

    Record that an SRE investigation has reached a terminal state.
    """
    if _investigations_total is None:
        return
    _safe_record(
        "investigations_total",
        lambda: _investigations_total.add(
            1,
            {
                "confidence_label": confidence_label,
                "approval_required": str(approval_required).lower(),
                "outcome": outcome,
            },
        ),
    )


def record_review_completed(*, confidence_label: str, outcome: str) -> None:
    """

    Record that a support review has reached a terminal state.
    """
    if _reviews_total is None:
        return
    _safe_record(
        "reviews_total",
        lambda: _reviews_total.add(
            1,
            {"confidence_label": confidence_label, "outcome": outcome},
        ),
    )


def record_pipeline_node_duration(
    *,
    pipeline: str,
    node: str,
    duration_seconds: float,
    status: str,
) -> None:
    """

    Record the wall-clock duration of a pipeline node execution.
    """
    if _pipeline_node_duration is None:
        return
    _safe_record(
        "pipeline_node_duration",
        lambda: _pipeline_node_duration.record(
            duration_seconds,
            {"pipeline": pipeline, "node": node, "status": status},
        ),
    )


def record_confidence_score(*, pipeline: str, score: float) -> None:
    """

    Record the confidence score produced by a DetermineConfidence node.
    """
    if _confidence_score is None:
        return
    _safe_record(
        "confidence_score",
        lambda: _confidence_score.record(score, {"pipeline": pipeline}),
    )


def record_approval_decision(*, decision: str, pipeline: str) -> None:
    """

    Record an approval decision (approve / reject / auto_approve).
    """
    if _approval_decisions_total is None:
        return
    _safe_record(
        "approval_decisions_total",
        lambda: _approval_decisions_total.add(
            1,
            {"decision": decision, "pipeline": pipeline},
        ),
    )


def record_llm_call(
    *,
    agent: str,
    model: str,
    duration_seconds: float,
    status: str,
) -> None:
    """

    Record a single LLM call dispatched via the LiteLLM gateway.
    """
    if _llm_calls_total is None or _llm_call_duration is None:
        return
    _safe_record(
        "llm_calls_total",
        lambda: _llm_calls_total.add(
            1,
            {"agent": agent, "model": model, "status": status},
        ),
    )
    _safe_record(
        "llm_call_duration",
        lambda: _llm_call_duration.record(
            duration_seconds,
            {"agent": agent, "model": model},
        ),
    )


def record_job_processed(
    *,
    job_type: str,
    outcome: str,
    duration_seconds: float,
) -> None:
    """

    Record a worker job that has reached a terminal state.
    """
    if _jobs_processed_total is None or _job_duration is None:
        return
    _safe_record(
        "jobs_processed_total",
        lambda: _jobs_processed_total.add(
            1,
            {"job_type": job_type, "outcome": outcome},
        ),
    )
    _safe_record(
        "job_duration",
        lambda: _job_duration.record(
            duration_seconds,
            {"job_type": job_type},
        ),
    )


def set_job_queue_depth(*, job_type: str, status: str, depth: int) -> None:
    """

    Set the current job queue depth gauge for a job type / status combination.
    """
    if _job_queue_depth is None:
        return
    _safe_record(
        "job_queue_depth",
        lambda: _job_queue_depth.set(
            depth,
            {"job_type": job_type, "status": status},
        ),
    )
```

- [ ] **Step 5: Run the no-op tests to confirm they pass**

```bash
just test tests/unit/utils/test_metrics.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Add tests verifying instruments are created and recorded against**

Append to `tests/unit/utils/test_metrics.py`:

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


class TestInitMeters:
    def setup_method(self):
        metrics.reset_meters()

    def teardown_method(self):
        metrics.reset_meters()

    def test_records_investigations_after_init(self):
        # Given meters initialised against an in-memory reader
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        meter = provider.get_meter("test")
        metrics.init_meters(meter=meter)

        # When recording an investigation
        metrics.record_investigation_completed(
            confidence_label="high",
            approval_required=False,
            outcome="completed",
        )

        # Then the counter is incremented
        data = reader.get_metrics_data()
        names = {
            m.name
            for rm in data.resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
        }
        assert "sentinel_investigations_total" in names

    def test_records_pipeline_node_duration(self):
        # Given meters initialised against an in-memory reader
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        meter = provider.get_meter("test")
        metrics.init_meters(meter=meter)

        # When recording a node duration
        metrics.record_pipeline_node_duration(
            pipeline="sre",
            node="classify_alert",
            duration_seconds=0.42,
            status="ok",
        )

        # Then the histogram observation is exported
        data = reader.get_metrics_data()
        names = {
            m.name
            for rm in data.resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
        }
        assert "sentinel_pipeline_node_duration_seconds" in names
```

- [ ] **Step 7: Run the new tests**

```bash
just test tests/unit/utils/test_metrics.py -v
```

Expected: all four tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/sentinel/utils/metrics.py tests/unit/utils/__init__.py tests/unit/utils/test_metrics.py
git commit -m "feat(metrics): add cross-cutting metrics recorder helpers"
```

---

## Phase 3: OpenTelemetry Bootstrap

### Task 4: Create bootstrap_otel module

**Files:**
- Create: `src/sentinel/bootstrap_otel.py`
- Create: `tests/unit/test_bootstrap_otel.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_bootstrap_otel.py`:

```python
from __future__ import annotations

from unittest import mock

from sentinel import bootstrap_otel
from sentinel.utils import metrics


class TestInitOtel:
    def teardown_method(self):
        metrics.reset_meters()

    def test_no_op_when_metrics_disabled(self):
        # Given metrics are disabled in settings
        with mock.patch.object(bootstrap_otel, "get_settings") as mock_settings:
            mock_settings.return_value.otel_metrics_enabled = False
            mock_settings.return_value.otel_service_name = "sentinel"

            # When init_otel is called
            bootstrap_otel.init_otel()

            # Then no meter is configured
            assert metrics._meter is None

    def test_initialises_meter_when_enabled(self):
        # Given metrics are enabled
        with mock.patch.object(bootstrap_otel, "get_settings") as mock_settings:
            mock_settings.return_value.otel_metrics_enabled = True
            mock_settings.return_value.otel_service_name = "sentinel-test"

            # When init_otel is called
            bootstrap_otel.init_otel()

            # Then the meter is set
            assert metrics._meter is not None

    def test_swallows_exceptions(self):
        # Given an init that raises
        with mock.patch.object(bootstrap_otel, "get_settings") as mock_settings:
            mock_settings.return_value.otel_metrics_enabled = True
            mock_settings.side_effect = RuntimeError("boom")

            # When init_otel is called — Then no exception escapes
            bootstrap_otel.init_otel()
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
just test tests/unit/test_bootstrap_otel.py -v
```

Expected: ImportError (`bootstrap_otel` does not exist).

- [ ] **Step 3: Create bootstrap_otel.py**

Create `src/sentinel/bootstrap_otel.py`:

```python
"""

OpenTelemetry initialisation.

`init_otel()` is called once during process startup (API lifespan or worker
main). It configures a MeterProvider with a Prometheus reader and applies
auto-instrumentation to FastAPI / SQLAlchemy / httpx / system metrics.

All failures are swallowed and logged — metrics must never block startup.
"""

from __future__ import annotations

from opentelemetry import metrics as otel_metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

from sentinel.settings import get_settings
from sentinel.utils import logs
from sentinel.utils import metrics as sentinel_metrics


_initialised = False


def init_otel() -> None:
    """

    Initialise OpenTelemetry metrics. Idempotent and exception-safe.
    """
    global _initialised
    try:
        settings = get_settings()
        if not settings.otel_metrics_enabled:
            logs.log_event("otel.disabled")
            return
        if _initialised:
            return

        resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
        reader = PrometheusMetricReader()
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        otel_metrics.set_meter_provider(provider)

        meter = otel_metrics.get_meter("sentinel")
        sentinel_metrics.init_meters(meter=meter)

        # Auto-instrumentation that does not require an app instance
        HTTPXClientInstrumentor().instrument()
        SystemMetricsInstrumentor().instrument()

        _initialised = True
        logs.log_event("otel.initialised", params={"service": settings.otel_service_name})
    except Exception as exc:
        logs.log_exception(exc, params={"step": "init_otel"})


def instrument_fastapi(app: object) -> None:
    """

    Apply OTel auto-instrumentation to a FastAPI app.

    Separate from init_otel() because the FastAPI instrumentor needs the app
    instance, which only exists after the app has been constructed.
    """
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
    except Exception as exc:
        logs.log_exception(exc, params={"step": "instrument_fastapi"})


def instrument_sqlalchemy(engine: object) -> None:
    """

    Apply OTel auto-instrumentation to a SQLAlchemy engine.
    """
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=engine)
    except Exception as exc:
        logs.log_exception(exc, params={"step": "instrument_sqlalchemy"})
```

Note: the FastAPI and SQLAlchemy instrumentors are imported inside the helper functions because they need to be applied late (after the app/engine exists). This is one of the rare cases where deferred imports are acceptable per the project rules — they're at function-level only because the modules they instrument may not be ready at import time. **However**, since the project's coding rules forbid inline imports, move them to module level instead:

Replace the function bodies above with this corrected version:

```python
"""

OpenTelemetry initialisation.

`init_otel()` is called once during process startup (API lifespan or worker
main). It configures a MeterProvider with a Prometheus reader and applies
auto-instrumentation to FastAPI / SQLAlchemy / httpx / system metrics.

All failures are swallowed and logged — metrics must never block startup.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import metrics as otel_metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

from sentinel.settings import get_settings
from sentinel.utils import logs
from sentinel.utils import metrics as sentinel_metrics


_initialised = False


def init_otel() -> None:
    """

    Initialise OpenTelemetry metrics. Idempotent and exception-safe.
    """
    global _initialised
    try:
        settings = get_settings()
        if not settings.otel_metrics_enabled:
            logs.log_event("otel.disabled")
            return
        if _initialised:
            return

        resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
        reader = PrometheusMetricReader()
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        otel_metrics.set_meter_provider(provider)

        meter = otel_metrics.get_meter("sentinel")
        sentinel_metrics.init_meters(meter=meter)

        HTTPXClientInstrumentor().instrument()
        SystemMetricsInstrumentor().instrument()

        _initialised = True
        logs.log_event(
            "otel.initialised",
            params={"service": settings.otel_service_name},
        )
    except Exception as exc:
        logs.log_exception(exc, params={"step": "init_otel"})


def instrument_fastapi(app: Any) -> None:
    """

    Apply OTel auto-instrumentation to a FastAPI app.
    """
    try:
        FastAPIInstrumentor.instrument_app(app)
    except Exception as exc:
        logs.log_exception(exc, params={"step": "instrument_fastapi"})


def instrument_sqlalchemy(engine: Any) -> None:
    """

    Apply OTel auto-instrumentation to a SQLAlchemy engine.
    """
    try:
        SQLAlchemyInstrumentor().instrument(engine=engine)
    except Exception as exc:
        logs.log_exception(exc, params={"step": "instrument_sqlalchemy"})
```

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
just test tests/unit/test_bootstrap_otel.py -v
```

Expected: all three tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/bootstrap_otel.py tests/unit/test_bootstrap_otel.py
git commit -m "feat(metrics): add OpenTelemetry initialisation bootstrap"
```

---

## Phase 4: Mount /metrics on the API

### Task 5: Wire init_otel into API lifespan and mount /metrics

**Files:**
- Modify: `src/sentinel/interfaces/api/app.py`
- Create: `tests/integration/interfaces/api/test_metrics_endpoint.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/interfaces/api/test_metrics_endpoint.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from sentinel.interfaces.api import app as api_app


class TestMetricsEndpoint:
    def test_returns_prometheus_exposition_format(self):
        # Given the FastAPI app
        with TestClient(api_app.app) as client:
            # When requesting /metrics
            response = client.get("/metrics")

            # Then the response is 200 and uses Prometheus exposition format
            assert response.status_code == 200
            content_type = response.headers["content-type"]
            assert "text/plain" in content_type

    def test_exposes_custom_sentinel_metric_names(self):
        # Given the FastAPI app with metrics initialised
        with TestClient(api_app.app) as client:
            # When fetching /metrics after recording an investigation
            from sentinel.utils import metrics

            metrics.record_investigation_completed(
                confidence_label="high",
                approval_required=False,
                outcome="completed",
            )
            response = client.get("/metrics")

            # Then the custom metric appears in the body
            assert "sentinel_investigations_total" in response.text
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
just test tests/integration/interfaces/api/test_metrics_endpoint.py -v
```

Expected: 404 — endpoint does not exist.

- [ ] **Step 3: Update app.py**

Replace the contents of `src/sentinel/interfaces/api/app.py` with:

```python
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import fastapi
from prometheus_client import make_asgi_app

from sentinel import bootstrap
from sentinel import bootstrap_otel
from sentinel.data import database
from sentinel.data import db as async_db
from sentinel.interfaces.api.routers.automations.router import router as automations_router
from sentinel.interfaces.api.routers.jobs.router import router as jobs_router
from sentinel.interfaces.api.routers.sre.router import router as sre_router
from sentinel.interfaces.api.routers.support.router import router as support_router
from sentinel.settings import get_settings
from sentinel.utils import logs


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI) -> AsyncGenerator[None]:
    bootstrap.initialise()
    bootstrap_otel.init_otel()

    if get_settings().database_url:
        engine = database.get_engine()
        bootstrap_otel.instrument_sqlalchemy(engine=engine.sync_engine)
        await async_db.connect_db()
        logs.log_event("database_engine_initialised")

    yield

    if get_settings().database_url:
        await async_db.disconnect_db()
    await database.close_engine()
    logs.log_event("database_engine_closed")


app = fastapi.FastAPI(
    title="Sentinel",
    description="AI SRE & AI Support Agent",
    version="0.1.0",
    lifespan=lifespan,
)

bootstrap_otel.instrument_fastapi(app=app)

app.mount("/metrics", make_asgi_app())

app.include_router(sre_router, prefix="/api")
app.include_router(support_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(automations_router, prefix="/api")


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "sentinel"}


@app.get("/", include_in_schema=False)
async def root() -> fastapi.responses.RedirectResponse:
    return fastapi.responses.RedirectResponse(url="/docs")
```

Note: `database.get_engine()` returns the async engine; the OTel SQLAlchemy instrumentor needs the underlying sync engine, accessed via `.sync_engine`. If `get_engine()` returns a sync engine instead, drop the `.sync_engine` access.

- [ ] **Step 4: Run the tests**

```bash
just test tests/integration/interfaces/api/test_metrics_endpoint.py -v
```

Expected: both tests pass. If `instrument_sqlalchemy` fails because of the engine attribute mismatch, check `src/sentinel/data/database.py` for the engine type and adjust the call accordingly. The instrumentor failure would be swallowed by `instrument_sqlalchemy`'s try/except, so this should not block the tests.

- [ ] **Step 5: Run the full lint to ensure import-linter contracts pass**

```bash
just lint
```

Expected: pass. If `bootstrap_otel` is in the wrong layer, move it (it should sit at the same layer as `bootstrap`, which is in the `bootstrap` layer per the import-linter contract).

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/interfaces/api/app.py tests/integration/interfaces/api/test_metrics_endpoint.py
git commit -m "feat(metrics): mount /metrics endpoint and init OTel in API lifespan"
```

---

## Phase 5: Pipeline Node Instrumentation

### Task 6: Create instrumented_node_run helper

**Files:**
- Create: `src/sentinel/interfaces/graphs/_node_helpers.py`
- Create: `tests/unit/interfaces/graphs/test_node_helpers.py`

- [ ] **Step 1: Inspect existing pipeline node structure**

```bash
sed -n '1,50p' src/sentinel/interfaces/graphs/sre_investigation.py
```

Note the imports and how nodes are defined (they extend `pydantic_graph.BaseNode` and define `async def run(self, ctx)`).

- [ ] **Step 2: Write the failing test**

Create `tests/unit/interfaces/graphs/test_node_helpers.py`:

```python
from __future__ import annotations

import asyncio
from unittest import mock

from sentinel.interfaces.graphs import _node_helpers
from sentinel.utils import metrics


class TestInstrumentedNodeRun:
    def test_records_duration_on_success(self):
        # Given an async function returning a value
        async def fake_run():
            return "result"

        # When wrapped and executed
        with mock.patch.object(metrics, "record_pipeline_node_duration") as recorder:
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="sre",
                node="classify_alert",
                fn=fake_run,
            )
            result = asyncio.run(wrapped())

        # Then the result is returned and the duration is recorded with status=ok
        assert result == "result"
        recorder.assert_called_once()
        kwargs = recorder.call_args.kwargs
        assert kwargs["pipeline"] == "sre"
        assert kwargs["node"] == "classify_alert"
        assert kwargs["status"] == "ok"
        assert kwargs["duration_seconds"] >= 0

    def test_records_error_status_on_exception(self):
        # Given an async function that raises
        async def fake_run():
            raise ValueError("boom")

        # When wrapped and executed
        with mock.patch.object(metrics, "record_pipeline_node_duration") as recorder:
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="sre",
                node="classify_alert",
                fn=fake_run,
            )
            try:
                asyncio.run(wrapped())
            except ValueError:
                pass

        # Then duration is recorded with status=error
        recorder.assert_called_once()
        assert recorder.call_args.kwargs["status"] == "error"
```

- [ ] **Step 3: Run the test to confirm it fails**

```bash
just test tests/unit/interfaces/graphs/test_node_helpers.py -v
```

Expected: ImportError.

- [ ] **Step 4: Create _node_helpers.py**

Create `src/sentinel/interfaces/graphs/_node_helpers.py`:

```python
"""

Helpers for instrumenting Pydantic Graph pipeline nodes.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sentinel.utils import metrics

T = TypeVar("T")


def instrumented_node_run(
    *,
    pipeline: str,
    node: str,
    fn: Callable[[], Awaitable[T]],
) -> Callable[[], Awaitable[T]]:
    """

    Wrap a node run callable to record its duration as a metric.

    Records duration with status=ok on normal return and status=error if the
    callable raises. The original exception is re-raised unchanged.
    """

    async def _runner() -> T:
        start = time.perf_counter()
        status = "ok"
        try:
            return await fn()
        except Exception:
            status = "error"
            raise
        finally:
            duration = time.perf_counter() - start
            metrics.record_pipeline_node_duration(
                pipeline=pipeline,
                node=node,
                duration_seconds=duration,
                status=status,
            )

    return _runner
```

- [ ] **Step 5: Run the tests**

```bash
just test tests/unit/interfaces/graphs/test_node_helpers.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/interfaces/graphs/_node_helpers.py tests/unit/interfaces/graphs/test_node_helpers.py
git commit -m "feat(metrics): add instrumented_node_run helper for pipeline nodes"
```

---

### Task 7: Instrument SRE pipeline nodes

**Files:**
- Modify: `src/sentinel/interfaces/graphs/sre_investigation.py`

- [ ] **Step 1: Read the current SRE pipeline file**

```bash
just test tests/functional -k sre --collect-only 2>&1 | head
```

Then open `src/sentinel/interfaces/graphs/sre_investigation.py` and locate each node class. There are 5 nodes per the design: ClassifyAlert, InvestigateWithHolmes, AnalyseRootCause, DetermineConfidence, PublishFindings.

- [ ] **Step 2: Wrap each node's run method**

For each of the 5 SRE node classes, replace the body of `async def run(self, ctx)` with a wrapper. Example pattern (apply to `ClassifyAlert`):

```python
async def run(self, ctx):
    from sentinel.interfaces.graphs._node_helpers import instrumented_node_run

    async def _impl():
        # ... original body of run() goes here ...
        return ...

    return await instrumented_node_run(
        pipeline="sre",
        node="classify_alert",
    fn=_impl)()
```

**Important:** the project rules forbid inline imports, so add `from sentinel.interfaces.graphs._node_helpers import instrumented_node_run` to the module-level imports at the top of `sre_investigation.py` instead, and use it directly without the inline import:

```python
async def run(self, ctx):
    async def _impl():
        # ... original body ...
        return ...

    return await instrumented_node_run(
        pipeline="sre",
        node="classify_alert",
        fn=_impl,
    )()
```

Apply this transformation to all five nodes with the following node names:
- `ClassifyAlert` → `node="classify_alert"`
- `InvestigateWithHolmes` → `node="investigate_with_holmes"`
- `AnalyseRootCause` → `node="analyse_root_cause"`
- `DetermineConfidence` → `node="determine_confidence"`
- `PublishFindings` → `node="publish_findings"`

- [ ] **Step 3: Add confidence score recording in DetermineConfidence**

Inside `DetermineConfidence.run()` body (the `_impl` you just created), after the confidence score is calculated and before returning, add:

```python
            metrics.record_confidence_score(
                pipeline="sre",
                score=confidence.score,
            )
```

(Use whatever the local variable name for the confidence score is — likely `confidence` or `result.confidence`.)

Add `from sentinel.utils import metrics` to the module-level imports if not already present.

- [ ] **Step 4: Add investigation outcome recording in PublishFindings**

Inside `PublishFindings.run()` body, just before returning, add:

```python
            metrics.record_investigation_completed(
                confidence_label=confidence.label.value if hasattr(confidence.label, "value") else str(confidence.label),
                approval_required=approval_required,
                outcome="completed",
            )
```

Use whatever local variables hold the confidence label and approval_required flag in this node. If they're not in scope, pull them from `ctx.state`.

- [ ] **Step 5: Run the SRE pipeline tests**

```bash
just test tests/unit/interfaces/graphs -v -k sre
just test tests/functional -v -k sre
```

Expected: all tests still pass. If a test breaks because the wrapper changes how exceptions propagate, debug — wrappers should be transparent.

- [ ] **Step 6: Run the full unit test suite as a sanity check**

```bash
just test
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/sentinel/interfaces/graphs/sre_investigation.py
git commit -m "feat(metrics): instrument SRE pipeline nodes with duration and outcome metrics"
```

---

### Task 8: Instrument Support pipeline nodes

**Files:**
- Modify: `src/sentinel/interfaces/graphs/support_review.py`

- [ ] **Step 1: Apply the same wrapper pattern to all 4 support nodes**

In `src/sentinel/interfaces/graphs/support_review.py`:

1. Add module-level imports:
   ```python
   from sentinel.interfaces.graphs._node_helpers import instrumented_node_run
   from sentinel.utils import metrics
   ```

2. Wrap each `run()` method with `instrumented_node_run` using these node names:
   - `ClassifyTicket` → `node="classify_ticket"`
   - `SearchDocumentation` → `node="search_documentation"`
   - `DraftResponse` → `node="draft_response"`
   - `DetermineConfidence` → `node="determine_confidence"`

3. In `DetermineConfidence.run()`, add:
   ```python
            metrics.record_confidence_score(
                pipeline="support",
                score=confidence.score,
            )
   ```

4. In the **terminal node** (likely `DetermineConfidence` since the support pipeline ends there per the design), record the review outcome:
   ```python
            metrics.record_review_completed(
                confidence_label=confidence.label.value if hasattr(confidence.label, "value") else str(confidence.label),
                outcome="completed",
            )
   ```

- [ ] **Step 2: Run support tests**

```bash
just test tests/unit/interfaces/graphs -v -k support
just test tests/functional -v -k support
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add src/sentinel/interfaces/graphs/support_review.py
git commit -m "feat(metrics): instrument support pipeline nodes with duration and outcome metrics"
```

---

## Phase 6: LLM and Approval Instrumentation

### Task 9: Instrument LiteLLM gateway call site

**Files:**
- Modify: `src/sentinel/interfaces/graphs/agents/utils.py`
- Create: `tests/unit/interfaces/graphs/agents/test_utils_metrics.py`

- [ ] **Step 1: Read the current agents/utils.py**

```bash
cat src/sentinel/interfaces/graphs/agents/utils.py
```

This file currently has `get_model_with_gateway()` (lines 4-22). It only constructs a model — it does NOT actually call the LLM. The LLM call happens inside Pydantic AI's `agent.run()`. To instrument the LLM call site without monkeypatching every agent, add a wrapper function that all agents must use instead of calling `agent.run()` directly.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/interfaces/graphs/agents/test_utils_metrics.py`:

```python
from __future__ import annotations

import asyncio
from unittest import mock

from sentinel.interfaces.graphs.agents import utils
from sentinel.utils import metrics


class TestRunAgentInstrumented:
    def test_records_llm_call_on_success(self):
        # Given a fake agent.run that returns a value
        fake_agent = mock.Mock()

        async def fake_run(*args, **kwargs):
            return mock.Mock(output="result")

        fake_agent.run = fake_run

        # When run_agent_instrumented is invoked
        with mock.patch.object(metrics, "record_llm_call") as recorder:
            asyncio.run(
                utils.run_agent_instrumented(
                    agent=fake_agent,
                    agent_name="alert_classifier",
                    model="openai/gpt-4.1-mini",
                    prompt="hello",
                )
            )

        # Then a successful LLM call metric is recorded
        recorder.assert_called_once()
        kwargs = recorder.call_args.kwargs
        assert kwargs["agent"] == "alert_classifier"
        assert kwargs["model"] == "openai/gpt-4.1-mini"
        assert kwargs["status"] == "ok"

    def test_records_error_status_when_agent_raises(self):
        # Given a fake agent.run that raises
        fake_agent = mock.Mock()

        async def fake_run(*args, **kwargs):
            raise RuntimeError("boom")

        fake_agent.run = fake_run

        # When run_agent_instrumented is invoked
        with mock.patch.object(metrics, "record_llm_call") as recorder:
            try:
                asyncio.run(
                    utils.run_agent_instrumented(
                        agent=fake_agent,
                        agent_name="alert_classifier",
                        model="openai/gpt-4.1-mini",
                        prompt="hello",
                    )
                )
            except RuntimeError:
                pass

        # Then an error LLM call metric is recorded
        recorder.assert_called_once()
        assert recorder.call_args.kwargs["status"] == "error"
```

- [ ] **Step 3: Run the test to confirm it fails**

```bash
just test tests/unit/interfaces/graphs/agents/test_utils_metrics.py -v
```

Expected: AttributeError — `run_agent_instrumented` does not exist.

- [ ] **Step 4: Add run_agent_instrumented to agents/utils.py**

Append to `src/sentinel/interfaces/graphs/agents/utils.py`:

```python
import time
from typing import Any

from sentinel.utils import metrics


async def run_agent_instrumented(
    *,
    agent: Any,
    agent_name: str,
    model: str,
    prompt: Any,
    **run_kwargs: Any,
) -> Any:
    """

    Run a Pydantic AI agent and record an LLM call metric.

    Wraps `agent.run(prompt, model=...)` with timing and labels. Re-raises
    any exception from the underlying agent unchanged.
    """
    start = time.perf_counter()
    status = "ok"
    try:
        return await agent.run(prompt, model=model, **run_kwargs)
    except Exception:
        status = "error"
        raise
    finally:
        duration = time.perf_counter() - start
        metrics.record_llm_call(
            agent=agent_name,
            model=model,
            duration_seconds=duration,
            status=status,
        )
```

Move the existing `import` statements to the top of the file if they aren't already there, and ensure all imports follow project rules (module-level only).

- [ ] **Step 5: Run the new test**

```bash
just test tests/unit/interfaces/graphs/agents/test_utils_metrics.py -v
```

Expected: pass.

- [ ] **Step 6: Update each agent call site to use run_agent_instrumented**

Find every place where `agent.run(...)` is called (except in tests):

```bash
grep -rn "agent.run(" src/sentinel/interfaces/graphs/ --include="*.py" | grep -v "test_"
```

For each call site, replace `await some_agent.run(prompt, model=ctx.deps.xyz_model)` with:

```python
await utils.run_agent_instrumented(
    agent=some_agent,
    agent_name="<agent name from list below>",
    model=ctx.deps.xyz_model,
    prompt=prompt,
)
```

Use these `agent_name` values (matching the metric label cardinality plan):
- alert_classifier
- root_cause_analyser
- ticket_reviewer
- response_drafter
- (any others discovered — name them after their file)

Ensure `from sentinel.interfaces.graphs.agents import utils` is at the top of each modified file.

- [ ] **Step 7: Run all graph and functional tests**

```bash
just test tests/unit/interfaces/graphs -v
just test tests/functional -v
```

Expected: pass. The functional tests monkeypatch agents (per CLAUDE.md), so the wrapper should be transparent.

- [ ] **Step 8: Commit**

```bash
git add src/sentinel/interfaces/graphs/agents/utils.py src/sentinel/interfaces/graphs/agents/*.py src/sentinel/interfaces/graphs/sre_investigation.py src/sentinel/interfaces/graphs/support_review.py tests/unit/interfaces/graphs/agents/test_utils_metrics.py
git commit -m "feat(metrics): instrument LiteLLM agent call site"
```

---

### Task 10: Instrument approval API endpoints

**Files:**
- Modify: `src/sentinel/interfaces/api/routers/sre/router.py`

- [ ] **Step 1: Read the current approval handlers**

```bash
sed -n '180,300p' src/sentinel/interfaces/api/routers/sre/router.py
```

Locate `approve_investigation()` (line 215) and `reject_investigation()` (line 256).

- [ ] **Step 2: Add metrics import at module level**

In the imports section of `src/sentinel/interfaces/api/routers/sre/router.py`, add:

```python
from sentinel.utils import metrics
```

- [ ] **Step 3: Record approval decisions**

In `approve_investigation()`, immediately before the return statement, add:

```python
    metrics.record_approval_decision(decision="approve", pipeline="sre")
```

In `reject_investigation()`, immediately before the return statement, add:

```python
    metrics.record_approval_decision(decision="reject", pipeline="sre")
```

Also locate where auto-approval happens (search the codebase):

```bash
grep -rn "auto.*approve\|approve.*auto" src/sentinel/ --include="*.py"
```

If there's a place where investigations bypass the approval gate due to high confidence (likely in `DetermineConfidence` or the approval flow inside the SRE pipeline), record there as well:

```python
    metrics.record_approval_decision(decision="auto_approve", pipeline="sre")
```

If the location is unclear, add a TODO comment in the spec discussion and proceed without auto_approve recording — it can be added in a follow-up.

- [ ] **Step 4: Write a quick integration check**

Add this test to `tests/integration/interfaces/api/test_metrics_endpoint.py`:

```python
    def test_approval_decision_metric_increments(self):
        # Given a recorded approve decision
        from sentinel.utils import metrics

        metrics.record_approval_decision(decision="approve", pipeline="sre")

        # When fetching /metrics
        with TestClient(api_app.app) as client:
            response = client.get("/metrics")

        # Then the metric appears
        assert "sentinel_approval_decisions_total" in response.text
```

- [ ] **Step 5: Run the test**

```bash
just test tests/integration/interfaces/api/test_metrics_endpoint.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/sentinel/interfaces/api/routers/sre/router.py tests/integration/interfaces/api/test_metrics_endpoint.py
git commit -m "feat(metrics): record approval decisions on SRE approve/reject endpoints"
```

---

## Phase 7: Worker Instrumentation

### Task 11: Add worker /metrics server

**Files:**
- Create: `src/sentinel/worker_metrics.py`
- Modify: `src/sentinel/worker.py`

- [ ] **Step 1: Inspect the worker entrypoint structure**

```bash
sed -n '1,50p' src/sentinel/worker.py
sed -n '200,320p' src/sentinel/worker.py
```

Note: poll loop starts at line 201, `main()` at line 311, dispatch at lines 72-114.

- [ ] **Step 2: Create worker_metrics.py**

Create `src/sentinel/worker_metrics.py`:

```python
"""

Standalone /metrics HTTP server for the worker process.

The worker has no FastAPI app of its own, so we expose Prometheus metrics
via a tiny Starlette app on a separate port.
"""

from __future__ import annotations

import asyncio

import uvicorn
from prometheus_client import make_asgi_app
from starlette.applications import Starlette
from starlette.routing import Mount

from sentinel.settings import get_settings
from sentinel.utils import logs


def build_app() -> Starlette:
    """

    Return a Starlette app exposing /metrics with the Prometheus exposition format.
    """
    return Starlette(routes=[Mount("/metrics", app=make_asgi_app())])


async def serve() -> None:
    """

    Run the worker metrics HTTP server until cancelled.
    """
    settings = get_settings()
    if not settings.otel_metrics_enabled:
        logs.log_event("worker_metrics.disabled")
        return
    config = uvicorn.Config(
        app=build_app(),
        host="0.0.0.0",
        port=settings.worker_metrics_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    logs.log_event(
        "worker_metrics.starting",
        params={"port": settings.worker_metrics_port},
    )
    try:
        await server.serve()
    except asyncio.CancelledError:
        logs.log_event("worker_metrics.stopped")
        raise
```

- [ ] **Step 3: Wire into worker.py**

Open `src/sentinel/worker.py`. At the top, add imports:

```python
from sentinel import bootstrap_otel
from sentinel import worker_metrics
```

In the worker's main async function (look for the function called from `main()` at line 311 — likely `_run()` or `_poll_loop()`), call `bootstrap_otel.init_otel()` once at startup before the main loop, and start the metrics server as a background task:

```python
    bootstrap_otel.init_otel()
    metrics_task = asyncio.create_task(worker_metrics.serve())
    try:
        await _poll_loop(...)
    finally:
        metrics_task.cancel()
        try:
            await metrics_task
        except asyncio.CancelledError:
            pass
```

The exact placement depends on the existing structure. Read the file carefully and place the calls so:
1. `init_otel()` runs once before any pipeline work
2. `worker_metrics.serve()` runs as a sibling task to the poll loop
3. On shutdown (the existing SIGTERM handling), the metrics task is cancelled

- [ ] **Step 4: Manual smoke test**

```bash
just run-worker &
WORKER_PID=$!
sleep 3
curl -s http://localhost:8001/metrics | head -20
kill $WORKER_PID
```

Expected: curl returns Prometheus exposition format including `process_*` and `python_gc_*` metrics.

If `just run-worker` doesn't exist, find the worker run command in the justfile (`grep worker justfile`) and use that.

- [ ] **Step 5: Commit**

```bash
git add src/sentinel/worker_metrics.py src/sentinel/worker.py
git commit -m "feat(metrics): add /metrics endpoint to worker process"
```

---

### Task 12: Instrument worker job execution loop

**Files:**
- Modify: `src/sentinel/worker.py`

- [ ] **Step 1: Locate the job execution function**

`_execute_job()` is at lines 83-114 of `src/sentinel/worker.py`.

- [ ] **Step 2: Add metrics import**

At the top of `worker.py`:

```python
from sentinel.utils import metrics
```

- [ ] **Step 3: Wrap _execute_job to record duration and outcome**

Find `_execute_job()` and wrap its body with timing + recording:

```python
async def _execute_job(*, db, job, ...) -> None:
    import time
    start = time.perf_counter()
    outcome = "success"
    try:
        # ... existing body of _execute_job ...
    except Exception:
        outcome = "failure"
        raise
    finally:
        duration = time.perf_counter() - start
        metrics.record_job_processed(
            job_type=job.job_type.value if hasattr(job.job_type, "value") else str(job.job_type),
            outcome=outcome,
            duration_seconds=duration,
        )
```

Move `import time` to the top of the file (module-level) per project rules.

- [ ] **Step 4: Add timeout outcome handling**

In the poll loop (lines 201-248), where `asyncio.wait_for(_execute_job(...), timeout=...)` is called, catch `asyncio.TimeoutError` and record:

```python
        except asyncio.TimeoutError:
            metrics.record_job_processed(
                job_type=job.job_type.value if hasattr(job.job_type, "value") else str(job.job_type),
                outcome="timeout",
                duration_seconds=settings.worker_job_timeout_seconds,
            )
            # ... existing timeout handling ...
            raise
```

If the timeout is already handled in `_execute_job` itself, place the recording there instead. Read the existing structure and pick the right place — only one location should record `timeout` to avoid double-counting.

- [ ] **Step 5: Add periodic queue depth gauge**

Add a small periodic task in the worker that updates the queue depth gauge every 15 seconds. In `worker.py`, add:

```python
async def _poll_queue_depth() -> None:
    """

    Periodically update the job queue depth gauge from the database.
    """
    while True:
        try:
            from sentinel.domain.jobs import queries as job_queries
            async with database.session() as db:
                depths = await job_queries.fetch_queue_depths(db=db)
                for row in depths:
                    metrics.set_job_queue_depth(
                        job_type=row.job_type,
                        status=row.status,
                        depth=row.depth,
                    )
        except Exception as exc:
            logs.log_exception(exc, params={"task": "poll_queue_depth"})
        await asyncio.sleep(15)
```

Move all imports to module level.

If `fetch_queue_depths` does not exist in `domain/jobs/queries.py`, create it:

```python
# In src/sentinel/domain/jobs/queries.py

import attrs

@attrs.frozen
class QueueDepthRow:
    job_type: str
    status: str
    depth: int


async def fetch_queue_depths(*, db) -> list[QueueDepthRow]:
    """

    Return current job queue depth grouped by job type and status.
    """
    from sqlalchemy import func, select
    from sentinel.data import job_table  # use the actual job table import path

    stmt = (
        select(
            job_table.c.job_type,
            job_table.c.status,
            func.count().label("depth"),
        )
        .group_by(job_table.c.job_type, job_table.c.status)
    )
    result = await db.execute(stmt)
    return [
        QueueDepthRow(job_type=row.job_type, status=row.status, depth=row.depth)
        for row in result.all()
    ]
```

Adjust the table import to the actual path used in the project (find with `grep -rn "job_table\|JobTable\|jobs_table" src/sentinel/data/`). Move all SQLAlchemy and table imports to the top of the file per project rules.

Then start the gauge poll task in the worker main, alongside `worker_metrics.serve()`:

```python
    metrics_task = asyncio.create_task(worker_metrics.serve())
    queue_depth_task = asyncio.create_task(_poll_queue_depth())
    try:
        await _poll_loop(...)
    finally:
        for task in (metrics_task, queue_depth_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
```

- [ ] **Step 6: Run worker tests**

```bash
just test tests/unit -k worker
just test tests/integration -k worker
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/sentinel/worker.py src/sentinel/domain/jobs/queries.py
git commit -m "feat(metrics): instrument worker job loop and queue depth gauge"
```

---

## Phase 8: Grafana Dashboards

### Task 13: Add Grafana dashboards provisioning config

**Files:**
- Create: `docker/grafana/provisioning/dashboards/sentinel.yaml`

- [ ] **Step 1: Create the provisioning config**

Create `docker/grafana/provisioning/dashboards/sentinel.yaml`:

```yaml
apiVersion: 1

providers:
  - name: sentinel
    orgId: 1
    folder: Sentinel
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: false
    options:
      path: /etc/grafana/provisioning/dashboards/sentinel
      foldersFromFilesStructure: false
```

- [ ] **Step 2: Verify the docker-compose grafana volume mount**

```bash
grep -A 5 grafana docker-compose.yml | head -30
```

Expected: confirm that `docker/grafana/provisioning` is mounted into `/etc/grafana/provisioning` inside the container. If not, add the volume mount.

- [ ] **Step 3: Commit**

```bash
git add docker/grafana/provisioning/dashboards/sentinel.yaml
git commit -m "feat(metrics): add Grafana dashboard provisioning config"
```

---

### Task 14: Add sentinel-overview dashboard

**Files:**
- Create: `docker/grafana/provisioning/dashboards/sentinel/sentinel-overview.json`

- [ ] **Step 1: Create the overview dashboard JSON**

Create `docker/grafana/provisioning/dashboards/sentinel/sentinel-overview.json`:

```json
{
  "title": "Sentinel — Overview",
  "uid": "sentinel-overview",
  "schemaVersion": 39,
  "version": 1,
  "tags": ["sentinel"],
  "timezone": "browser",
  "time": {"from": "now-1h", "to": "now"},
  "refresh": "30s",
  "panels": [
    {
      "id": 1,
      "type": "row",
      "title": "Health",
      "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0}
    },
    {
      "id": 2,
      "type": "stat",
      "title": "API Request Rate",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 6, "x": 0, "y": 1},
      "targets": [
        {"expr": "sum(rate(http_server_request_duration_seconds_count[5m]))", "refId": "A"}
      ]
    },
    {
      "id": 3,
      "type": "stat",
      "title": "API p95 Latency (s)",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 6, "x": 6, "y": 1},
      "targets": [
        {"expr": "histogram_quantile(0.95, sum(rate(http_server_request_duration_seconds_bucket[5m])) by (le))", "refId": "A"}
      ]
    },
    {
      "id": 4,
      "type": "stat",
      "title": "API Error Rate %",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 6, "x": 12, "y": 1},
      "targets": [
        {"expr": "100 * sum(rate(http_server_request_duration_seconds_count{status_code=~\"5..\"}[5m])) / sum(rate(http_server_request_duration_seconds_count[5m]))", "refId": "A"}
      ]
    },
    {
      "id": 5,
      "type": "stat",
      "title": "Worker Job Success Rate %",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 6, "x": 18, "y": 1},
      "targets": [
        {"expr": "100 * sum(rate(sentinel_jobs_processed_total{outcome=\"success\"}[5m])) / sum(rate(sentinel_jobs_processed_total[5m]))", "refId": "A"}
      ]
    },
    {
      "id": 10,
      "type": "row",
      "title": "Throughput (24h)",
      "gridPos": {"h": 1, "w": 24, "x": 0, "y": 5}
    },
    {
      "id": 11,
      "type": "stat",
      "title": "Investigations Completed",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 8, "x": 0, "y": 6},
      "targets": [
        {"expr": "sum(increase(sentinel_investigations_total[24h]))", "refId": "A"}
      ]
    },
    {
      "id": 12,
      "type": "stat",
      "title": "Reviews Completed",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 8, "x": 8, "y": 6},
      "targets": [
        {"expr": "sum(increase(sentinel_reviews_total[24h]))", "refId": "A"}
      ]
    },
    {
      "id": 13,
      "type": "stat",
      "title": "Approvals Pending",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 8, "x": 16, "y": 6},
      "targets": [
        {"expr": "sum(sentinel_job_queue_depth{status=\"pending\"})", "refId": "A"}
      ]
    },
    {
      "id": 20,
      "type": "row",
      "title": "Quality",
      "gridPos": {"h": 1, "w": 24, "x": 0, "y": 10}
    },
    {
      "id": 21,
      "type": "stat",
      "title": "Avg Confidence — SRE",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 6, "x": 0, "y": 11},
      "targets": [
        {"expr": "sum(rate(sentinel_confidence_score_sum{pipeline=\"sre\"}[1h])) / sum(rate(sentinel_confidence_score_count{pipeline=\"sre\"}[1h]))", "refId": "A"}
      ]
    },
    {
      "id": 22,
      "type": "stat",
      "title": "Avg Confidence — Support",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 6, "x": 6, "y": 11},
      "targets": [
        {"expr": "sum(rate(sentinel_confidence_score_sum{pipeline=\"support\"}[1h])) / sum(rate(sentinel_confidence_score_count{pipeline=\"support\"}[1h]))", "refId": "A"}
      ]
    },
    {
      "id": 23,
      "type": "stat",
      "title": "Approval Rate %",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 6, "x": 12, "y": 11},
      "targets": [
        {"expr": "100 * sum(rate(sentinel_approval_decisions_total{decision=\"approve\"}[1h])) / sum(rate(sentinel_approval_decisions_total[1h]))", "refId": "A"}
      ]
    },
    {
      "id": 24,
      "type": "stat",
      "title": "Auto-Approval Rate %",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 4, "w": 6, "x": 18, "y": 11},
      "targets": [
        {"expr": "100 * sum(rate(sentinel_approval_decisions_total{decision=\"auto_approve\"}[1h])) / sum(rate(sentinel_approval_decisions_total[1h]))", "refId": "A"}
      ]
    },
    {
      "id": 30,
      "type": "row",
      "title": "Drill-down",
      "gridPos": {"h": 1, "w": 24, "x": 0, "y": 15}
    },
    {
      "id": 31,
      "type": "text",
      "title": "Detail dashboards",
      "gridPos": {"h": 4, "w": 24, "x": 0, "y": 16},
      "options": {
        "mode": "markdown",
        "content": "- [SRE pipeline detail](/d/sentinel-sre)\n- [Support pipeline detail](/d/sentinel-support)\n- [Worker & queue detail](/d/sentinel-worker)"
      }
    }
  ]
}
```

- [ ] **Step 2: Validate JSON syntax**

```bash
.venv/bin/python -c "import json; json.load(open('docker/grafana/provisioning/dashboards/sentinel/sentinel-overview.json'))"
```

Expected: no output (valid JSON).

- [ ] **Step 3: Commit**

```bash
git add docker/grafana/provisioning/dashboards/sentinel/sentinel-overview.json
git commit -m "feat(metrics): add Sentinel overview Grafana dashboard"
```

---

### Task 15: Add sentinel-sre dashboard

**Files:**
- Create: `docker/grafana/provisioning/dashboards/sentinel/sentinel-sre.json`

- [ ] **Step 1: Create the SRE dashboard JSON**

Create `docker/grafana/provisioning/dashboards/sentinel/sentinel-sre.json`:

```json
{
  "title": "Sentinel — SRE Pipeline",
  "uid": "sentinel-sre",
  "schemaVersion": 39,
  "version": 1,
  "tags": ["sentinel", "sre"],
  "timezone": "browser",
  "time": {"from": "now-1h", "to": "now"},
  "refresh": "30s",
  "panels": [
    {
      "id": 1,
      "type": "timeseries",
      "title": "Investigation Rate by Confidence",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
      "targets": [
        {"expr": "sum by (confidence_label) (rate(sentinel_investigations_total[5m]))", "legendFormat": "{{confidence_label}}", "refId": "A"}
      ]
    },
    {
      "id": 2,
      "type": "heatmap",
      "title": "Pipeline Node Duration (p95)",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
      "targets": [
        {"expr": "histogram_quantile(0.95, sum by (le, node) (rate(sentinel_pipeline_node_duration_seconds_bucket{pipeline=\"sre\"}[5m])))", "legendFormat": "{{node}}", "refId": "A"}
      ]
    },
    {
      "id": 3,
      "type": "histogram",
      "title": "Confidence Score Distribution",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8},
      "targets": [
        {"expr": "sum(rate(sentinel_confidence_score_bucket{pipeline=\"sre\"}[1h])) by (le)", "refId": "A"}
      ]
    },
    {
      "id": 4,
      "type": "barchart",
      "title": "Approval Decisions",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8},
      "targets": [
        {"expr": "sum by (decision) (increase(sentinel_approval_decisions_total{pipeline=\"sre\"}[1h]))", "legendFormat": "{{decision}}", "refId": "A"}
      ]
    },
    {
      "id": 5,
      "type": "timeseries",
      "title": "LLM Calls by Agent",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 16},
      "targets": [
        {"expr": "sum by (agent) (rate(sentinel_llm_calls_total{agent=~\"alert_classifier|root_cause_analyser\"}[5m]))", "legendFormat": "{{agent}}", "refId": "A"}
      ]
    },
    {
      "id": 6,
      "type": "timeseries",
      "title": "LLM Call p95 Latency",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 16},
      "targets": [
        {"expr": "histogram_quantile(0.95, sum by (le, agent) (rate(sentinel_llm_call_duration_seconds_bucket{agent=~\"alert_classifier|root_cause_analyser\"}[5m])))", "legendFormat": "{{agent}}", "refId": "A"}
      ]
    },
    {
      "id": 7,
      "type": "table",
      "title": "Failure Rate by Node",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 24, "x": 0, "y": 24},
      "targets": [
        {"expr": "100 * sum by (node) (rate(sentinel_pipeline_node_duration_seconds_count{pipeline=\"sre\",status=\"error\"}[5m])) / sum by (node) (rate(sentinel_pipeline_node_duration_seconds_count{pipeline=\"sre\"}[5m]))", "format": "table", "refId": "A"}
      ]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON**

```bash
.venv/bin/python -c "import json; json.load(open('docker/grafana/provisioning/dashboards/sentinel/sentinel-sre.json'))"
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add docker/grafana/provisioning/dashboards/sentinel/sentinel-sre.json
git commit -m "feat(metrics): add Sentinel SRE pipeline Grafana dashboard"
```

---

### Task 16: Add sentinel-support dashboard

**Files:**
- Create: `docker/grafana/provisioning/dashboards/sentinel/sentinel-support.json`

- [ ] **Step 1: Create the support dashboard JSON**

Create `docker/grafana/provisioning/dashboards/sentinel/sentinel-support.json`:

```json
{
  "title": "Sentinel — Support Pipeline",
  "uid": "sentinel-support",
  "schemaVersion": 39,
  "version": 1,
  "tags": ["sentinel", "support"],
  "timezone": "browser",
  "time": {"from": "now-1h", "to": "now"},
  "refresh": "30s",
  "panels": [
    {
      "id": 1,
      "type": "timeseries",
      "title": "Review Rate by Confidence",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
      "targets": [
        {"expr": "sum by (confidence_label) (rate(sentinel_reviews_total[5m]))", "legendFormat": "{{confidence_label}}", "refId": "A"}
      ]
    },
    {
      "id": 2,
      "type": "heatmap",
      "title": "Pipeline Node Duration (p95)",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
      "targets": [
        {"expr": "histogram_quantile(0.95, sum by (le, node) (rate(sentinel_pipeline_node_duration_seconds_bucket{pipeline=\"support\"}[5m])))", "legendFormat": "{{node}}", "refId": "A"}
      ]
    },
    {
      "id": 3,
      "type": "histogram",
      "title": "Confidence Score Distribution",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8},
      "targets": [
        {"expr": "sum(rate(sentinel_confidence_score_bucket{pipeline=\"support\"}[1h])) by (le)", "refId": "A"}
      ]
    },
    {
      "id": 4,
      "type": "timeseries",
      "title": "LLM Calls by Agent",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8},
      "targets": [
        {"expr": "sum by (agent) (rate(sentinel_llm_calls_total{agent=~\"ticket_reviewer|response_drafter\"}[5m]))", "legendFormat": "{{agent}}", "refId": "A"}
      ]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON and commit**

```bash
.venv/bin/python -c "import json; json.load(open('docker/grafana/provisioning/dashboards/sentinel/sentinel-support.json'))"
git add docker/grafana/provisioning/dashboards/sentinel/sentinel-support.json
git commit -m "feat(metrics): add Sentinel support pipeline Grafana dashboard"
```

---

### Task 17: Add sentinel-worker dashboard

**Files:**
- Create: `docker/grafana/provisioning/dashboards/sentinel/sentinel-worker.json`

- [ ] **Step 1: Create the worker dashboard JSON**

Create `docker/grafana/provisioning/dashboards/sentinel/sentinel-worker.json`:

```json
{
  "title": "Sentinel — Worker & Queue",
  "uid": "sentinel-worker",
  "schemaVersion": 39,
  "version": 1,
  "tags": ["sentinel", "worker"],
  "timezone": "browser",
  "time": {"from": "now-1h", "to": "now"},
  "refresh": "30s",
  "panels": [
    {
      "id": 1,
      "type": "timeseries",
      "title": "Job Queue Depth",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 24, "x": 0, "y": 0},
      "targets": [
        {"expr": "sum by (job_type, status) (sentinel_job_queue_depth)", "legendFormat": "{{job_type}}/{{status}}", "refId": "A"}
      ]
    },
    {
      "id": 2,
      "type": "timeseries",
      "title": "Job Processing Rate",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8},
      "targets": [
        {"expr": "sum by (job_type, outcome) (rate(sentinel_jobs_processed_total[5m]))", "legendFormat": "{{job_type}}/{{outcome}}", "refId": "A"}
      ]
    },
    {
      "id": 3,
      "type": "timeseries",
      "title": "Job Duration p50/p95/p99",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8},
      "targets": [
        {"expr": "histogram_quantile(0.50, sum by (le, job_type) (rate(sentinel_job_duration_seconds_bucket[5m])))", "legendFormat": "{{job_type}} p50", "refId": "A"},
        {"expr": "histogram_quantile(0.95, sum by (le, job_type) (rate(sentinel_job_duration_seconds_bucket[5m])))", "legendFormat": "{{job_type}} p95", "refId": "B"},
        {"expr": "histogram_quantile(0.99, sum by (le, job_type) (rate(sentinel_job_duration_seconds_bucket[5m])))", "legendFormat": "{{job_type}} p99", "refId": "C"}
      ]
    },
    {
      "id": 4,
      "type": "timeseries",
      "title": "Worker CPU & Memory",
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "gridPos": {"h": 8, "w": 24, "x": 0, "y": 16},
      "targets": [
        {"expr": "rate(process_cpu_seconds_total{job=\"sentinel-worker\"}[5m])", "legendFormat": "CPU", "refId": "A"},
        {"expr": "process_resident_memory_bytes{job=\"sentinel-worker\"}", "legendFormat": "RSS bytes", "refId": "B"}
      ]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON and commit**

```bash
.venv/bin/python -c "import json; json.load(open('docker/grafana/provisioning/dashboards/sentinel/sentinel-worker.json'))"
git add docker/grafana/provisioning/dashboards/sentinel/sentinel-worker.json
git commit -m "feat(metrics): add Sentinel worker Grafana dashboard"
```

---

### Task 18: Dashboard schema and metric drift validation test

**Files:**
- Create: `tests/integration/test_grafana_dashboards.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_grafana_dashboards.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "docker" / "grafana" / "provisioning" / "dashboards" / "sentinel"

KNOWN_METRIC_PREFIXES = (
    "sentinel_investigations_total",
    "sentinel_reviews_total",
    "sentinel_pipeline_node_duration_seconds",
    "sentinel_confidence_score",
    "sentinel_approval_decisions_total",
    "sentinel_llm_calls_total",
    "sentinel_llm_call_duration_seconds",
    "sentinel_jobs_processed_total",
    "sentinel_job_duration_seconds",
    "sentinel_job_queue_depth",
    "http_server_request_duration_seconds",
    "process_cpu_seconds_total",
    "process_resident_memory_bytes",
)


class TestGrafanaDashboards:
    def test_all_dashboards_are_valid_json(self):
        # Given the dashboards directory
        files = sorted(DASHBOARD_DIR.glob("*.json"))
        assert files, "no dashboard files found"

        # When loading each one
        for path in files:
            # Then it parses as valid JSON
            with path.open() as fh:
                data = json.load(fh)
            assert "title" in data
            assert "uid" in data
            assert "panels" in data

    def test_all_dashboard_promql_references_known_metrics(self):
        # Given the dashboards directory
        files = sorted(DASHBOARD_DIR.glob("*.json"))

        # When extracting every PromQL expr
        unknown: list[str] = []
        for path in files:
            with path.open() as fh:
                data = json.load(fh)
            for panel in data.get("panels", []):
                for target in panel.get("targets", []):
                    expr = target.get("expr", "")
                    if not expr:
                        continue
                    # Then every metric name in the expr starts with a known prefix
                    if not any(prefix in expr for prefix in KNOWN_METRIC_PREFIXES):
                        unknown.append(f"{path.name}: {expr}")

        assert not unknown, f"Dashboards reference unknown metrics: {unknown}"
```

- [ ] **Step 2: Run the test**

```bash
just test tests/integration/test_grafana_dashboards.py -v
```

Expected: both tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_grafana_dashboards.py
git commit -m "test(metrics): validate Grafana dashboard JSON and metric references"
```

---

## Phase 9: Prometheus Scrape & Helm

### Task 19: Update Prometheus scrape config for worker

**Files:**
- Modify: `docker/prometheus/prometheus.yml`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the worker scrape job**

Replace `docker/prometheus/prometheus.yml` with:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "sentinel-api"
    metrics_path: /metrics
    static_configs:
      - targets: ["api:8000"]
        labels:
          service: sentinel-api

  - job_name: "sentinel-worker"
    metrics_path: /metrics
    static_configs:
      - targets: ["worker:8001"]
        labels:
          service: sentinel-worker

  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
```

- [ ] **Step 2: Ensure docker-compose has a worker service exposing 8001**

Check `docker-compose.yml`:

```bash
grep -A 20 "  worker:" docker-compose.yml
```

If a `worker` service doesn't exist, add one (mirror the `api` service config but use the worker entry command). If it exists, ensure it exposes port 8001:

```yaml
  worker:
    # ... existing config ...
    ports:
      - "8001:8001"
```

If the worker doesn't currently exist in docker-compose, add it as a sibling of `api`:

```yaml
  worker:
    build: .
    command: ["python", "-m", "sentinel.worker"]
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@db:5432/sentinel
      OTEL_METRICS_ENABLED: "true"
      WORKER_METRICS_PORT: "8001"
    depends_on:
      - db
    ports:
      - "8001:8001"
```

(Match other env-var conventions used by the existing `api` service in the file.)

- [ ] **Step 3: Smoke test with docker-compose**

```bash
just docker-compose-up &
sleep 20
curl -s http://localhost:9090/api/v1/targets | grep -E "(sentinel-api|sentinel-worker)"
just docker-compose-down
```

Expected: both targets appear in Prometheus targets.

- [ ] **Step 4: Commit**

```bash
git add docker/prometheus/prometheus.yml docker-compose.yml
git commit -m "feat(metrics): scrape worker /metrics from Prometheus"
```

---

### Task 20: Helm chart Prometheus annotations

**Files:**
- Modify: `helm/sentinel/templates/service.yaml`
- Modify: `helm/sentinel/templates/deployment.yaml`

- [ ] **Step 1: Add annotations to API deployment pod template**

In `helm/sentinel/templates/deployment.yaml`, find the API deployment pod template metadata and add annotations:

```yaml
    metadata:
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: "/metrics"
```

(Merge with existing annotations if present.)

- [ ] **Step 2: Add annotations to worker deployment pod template**

In the worker deployment pod template metadata in the same file:

```yaml
    metadata:
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8001"
        prometheus.io/path: "/metrics"
```

- [ ] **Step 3: Add a worker metrics Service**

Append to `helm/sentinel/templates/service.yaml`:

```yaml
---
apiVersion: v1
kind: Service
metadata:
  name: {{ include "sentinel.fullname" . }}-worker-metrics
  labels:
    {{- include "sentinel.labels" . | nindent 4 }}
    component: worker
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "8001"
    prometheus.io/path: "/metrics"
spec:
  type: ClusterIP
  ports:
    - port: 8001
      targetPort: 8001
      protocol: TCP
      name: metrics
  selector:
    {{- include "sentinel.selectorLabels" . | nindent 4 }}
    component: worker
```

- [ ] **Step 4: Validate Helm template renders**

```bash
helm template helm/sentinel | grep -A 5 prometheus.io/scrape
```

Expected: annotations appear in the rendered output.

- [ ] **Step 5: Commit**

```bash
git add helm/sentinel/templates/service.yaml helm/sentinel/templates/deployment.yaml
git commit -m "feat(metrics): add Prometheus annotations and worker metrics service to Helm chart"
```

---

## Phase 10: Documentation

### Task 21: Update PRD and architecture docs

**Files:**
- Modify: `docs/prd.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update PRD §4 acceptance criteria**

In `docs/prd.md`, find section "4. Observability & Feedback Loop". The Datadog APM line is currently:

```
- [ ] Datadog APM integration for distributed tracing across the pipeline
```

Replace with:

```
- [x] OpenTelemetry metrics exposed at `/metrics` (API and worker), Prometheus + Grafana dashboards (overview / SRE / Support / Worker)
- [ ] OpenTelemetry tracing → Tempo (deferred — SDK initialised, traces not yet instrumented)
```

In the "Remaining Gaps" table, replace the `Datadog APM distributed tracing` row with:

```
| OTel tracing → Tempo | OTel SDK initialised; need to add TracerProvider + node/LLM span instrumentation | Phase B |
```

- [ ] **Step 2: Add a "Future Work" entry for Terraform-managed dashboards**

Append to the "Remaining Gaps" / "Future Work" area of `docs/prd.md`:

```
### Future Work — Observability

- **Terraform-managed Grafana dashboards.** When Sentinel is deployed against multiple shared Grafana instances or needs centralised RBAC/folder-as-code, migrate from JSON provisioning to the Grafana Terraform provider. Triggers: a second consumer of prod Grafana, org-level RBAC needs, or dev/prod dashboard drift becoming a problem.
```

- [ ] **Step 3: Add an observability section to architecture.md**

Append to `docs/architecture.md` (under an existing "Observability" heading if present, otherwise create one):

```markdown
## Metrics & Dashboards

Metrics are emitted via OpenTelemetry SDK with a Prometheus exporter, exposed on `/metrics` for both the API (port 8000) and the worker (port 8001 via a small Starlette sidecar app inside the worker process).

The recorder helpers live in `src/sentinel/utils/metrics.py` and follow the same cross-cutting pattern as `utils/logs.py` — they're imported directly throughout the domain and pipeline layers. All recorder helpers swallow exceptions and no-op when `OTEL_METRICS_ENABLED=false`.

Pipeline node duration is instrumented via a single `instrumented_node_run` wrapper in `interfaces/graphs/_node_helpers.py`, applied to every Pydantic Graph node. LLM calls are instrumented at the single chokepoint `interfaces/graphs/agents/utils.py::run_agent_instrumented` which all agent runs route through.

Grafana dashboards are JSON files under `docker/grafana/provisioning/dashboards/sentinel/`, auto-loaded by Grafana via file-based provisioning. The hierarchy is: `sentinel-overview` (top-level health + business KPIs) → `sentinel-sre` / `sentinel-support` / `sentinel-worker` (detail dashboards).
```

- [ ] **Step 4: Run the docs update flow if applicable**

```bash
just lint
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add docs/prd.md docs/architecture.md
git commit -m "docs: update PRD and architecture for OTel metrics and Grafana dashboards"
```

---

## Phase 11: Final Validation

### Task 22: End-to-end smoke test and full lint

**Files:**
- (no new files; verification only)

- [ ] **Step 1: Run the full unit test suite**

```bash
just test
```

Expected: all tests pass.

- [ ] **Step 2: Run the full lint**

```bash
just lint
```

Expected: ruff + mypy + import-linter all pass.

- [ ] **Step 3: Run integration tests if database is available**

```bash
just test-integration
```

Expected: pass.

- [ ] **Step 4: Bring up docker-compose and verify the full flow**

```bash
just docker-compose-up
sleep 20

# Verify API /metrics
curl -s http://localhost:8000/metrics | grep sentinel_

# Verify worker /metrics
curl -s http://localhost:8001/metrics | grep process_

# Verify Prometheus targets
curl -s http://localhost:9090/api/v1/targets | python -c "import sys, json; data = json.load(sys.stdin); print([t['labels']['job'] + ':' + t['health'] for t in data['data']['activeTargets']])"

# Verify Grafana dashboards loaded
curl -s -u admin:admin http://localhost:3000/api/search?query=Sentinel
```

Expected:
- API `/metrics` returns sentinel_ prefixed metrics
- Worker `/metrics` returns process_ metrics
- Both Prometheus targets are `up`
- Grafana returns 4 dashboards in the Sentinel folder

- [ ] **Step 5: Tear down docker-compose**

```bash
just docker-compose-down
```

- [ ] **Step 6: Final commit if anything was tweaked**

If the smoke test surfaced any small fixes:

```bash
git add -p
git commit -m "fix(metrics): <describe fix>"
```

Otherwise, no commit needed.

---

## Self-Review Notes

**Spec coverage check** (against `docs/superpowers/specs/2026-04-07-grafana-metrics-design.md`):

| Spec section | Implemented in task |
|---|---|
| OTel SDK + Prometheus exporter | Task 1, 4 |
| `/metrics` endpoint on API | Task 5 |
| `/metrics` endpoint on worker | Task 11 |
| `utils/metrics.py` recorder helpers | Task 3 |
| `bootstrap_otel.py` | Task 4 |
| Pipeline node duration histogram | Task 6, 7, 8 |
| `sentinel_investigations_total` | Task 7 |
| `sentinel_reviews_total` | Task 8 |
| `sentinel_confidence_score` | Task 7, 8 |
| `sentinel_approval_decisions_total` | Task 10 |
| `sentinel_llm_calls_total` + duration | Task 9 |
| `sentinel_jobs_processed_total` + duration | Task 12 |
| `sentinel_job_queue_depth` gauge | Task 12 |
| HTTP auto-instrumentation | Task 4 (via OTel FastAPI instrumentor) |
| Process / runtime metrics | Task 4 (via SystemMetricsInstrumentor) |
| 4 Grafana dashboards (overview/sre/support/worker) | Task 14, 15, 16, 17 |
| Dashboard provisioning config | Task 13 |
| Prometheus scrape config update | Task 19 |
| Helm chart annotations + worker metrics service | Task 20 |
| Test: unit metrics helpers + no-op | Task 3 |
| Test: bootstrap_otel | Task 4 |
| Test: `/metrics` integration | Task 5 |
| Test: dashboard schema + metric drift | Task 18 |
| PRD + architecture docs | Task 21 |
| Future Work: Terraform dashboards | Task 21 |
| `OTEL_METRICS_ENABLED` env var | Task 2 |

All spec sections are covered.

**Type / signature consistency check:**
- `record_*` helper signatures in Task 3 match call sites in Tasks 7, 8, 9, 10, 12 ✓
- `instrumented_node_run` signature in Task 6 matches usage in Tasks 7, 8 ✓
- `run_agent_instrumented` signature in Task 9 matches the call-site replacement instructions ✓
- `set_job_queue_depth` matches the `_poll_queue_depth` task body ✓
- Module name `bootstrap_otel` is consistent across Tasks 4, 5, 11 ✓

**Known risks / things to verify during execution:**
1. Task 5 `instrument_sqlalchemy(engine.sync_engine)` — the `.sync_engine` attribute may not exist on the project's engine wrapper. The instrumentor failure is swallowed, so this won't break startup, but it may mean SQLAlchemy spans/metrics don't appear. If you find a different attribute name during execution (e.g., the engine itself is sync, or has a different unwrap method), update the call.
2. Task 12 `fetch_queue_depths` — the actual job table import path needs to be discovered during execution. The placeholder uses `sentinel.data.job_table` which may not be correct.
3. Task 9 — every `agent.run(...)` call site must be migrated. There may be more than the 4 listed agents. Use `grep` to find them all.
4. Task 7 `PublishFindings` — the local variable names for `confidence`, `approval_required`, etc. depend on the actual node body. Read the file before editing.
