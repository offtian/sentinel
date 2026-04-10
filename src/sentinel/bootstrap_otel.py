"""

OpenTelemetry initialisation.

`init_otel()` is called once during process startup (API lifespan or worker
main). It configures a MeterProvider with a Prometheus reader and applies
auto-instrumentation to FastAPI / SQLAlchemy / httpx / system metrics.

All failures are swallowed and logged — metrics must never block startup.
"""

from __future__ import annotations

import os
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
_traces_initialised = False


def init_otel() -> None:
    """

    Initialise OpenTelemetry metrics. Idempotent and exception-safe.
    """
    global _initialised  # noqa: PLW0603
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


def init_traces() -> None:
    """
    Initialise OpenTelemetry traces via Logfire SDK.

    Configure Logfire with ``send_to_logfire=False`` so spans export to the
    OTLP endpoint (Tempo) specified by ``otel_traces_endpoint`` instead of
    Logfire cloud.  PydanticAI agents with ``instrument=True`` then emit
    enriched spans automatically.

    Idempotent and exception-safe.
    """
    global _traces_initialised  # noqa: PLW0603
    try:
        settings = get_settings()
        if not settings.otel_traces_enabled or not settings.otel_traces_endpoint:
            logs.log_event("otel.traces.disabled")
            return
        if _traces_initialised:
            return

        import logfire

        os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", settings.otel_traces_endpoint)

        logfire.configure(
            send_to_logfire=False,
            service_name=settings.otel_service_name,
        )

        _traces_initialised = True
        logs.log_event(
            "otel.traces.initialised",
            params={
                "backend": "logfire-sdk",
                "endpoint": settings.otel_traces_endpoint,
                "service": settings.otel_service_name,
            },
        )
    except Exception as exc:
        logs.log_exception(exc, params={"step": "init_traces"})
