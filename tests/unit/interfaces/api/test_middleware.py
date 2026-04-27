"""Unit tests for ``RequestIdMiddleware`` (F2.2 / F2.3).

Cover header parsing, structlog contextvars binding, OTel span attribute
propagation, and response-header echo. The middleware is exercised
against a minimal isolated ``FastAPI`` app so the project-wide DB
lifespan does not run.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest import mock

import fastapi
import pytest
import structlog
from fastapi.testclient import TestClient
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sentinel.interfaces.api import app as api_app
from sentinel.interfaces.api import middleware as middleware_mod


_VALID_UUID = "12345678-1234-5678-1234-567812345678"

# OTEL_SDK_DISABLED short-circuits SDK construction to no-op spans, so the
# in-memory exporter never sees finished spans even when the test wires
# its own TracerProvider. CI does not set the env var; local dev sets it
# to silence "Connection refused" noise from OTel exporters that target
# the optional Langfuse stack. Skip cleanly so local runs aren't a false
# positive.
_skip_when_otel_disabled = pytest.mark.skipif(
    os.environ.get("OTEL_SDK_DISABLED", "").lower() in {"true", "1"},
    reason="OTEL_SDK_DISABLED short-circuits SDK construction; spans never finish.",
)


def _build_test_app(*, capture: dict[str, Any] | None = None) -> fastapi.FastAPI:
    """Return a minimal FastAPI app wired with ``RequestIdMiddleware``.

    The optional ``capture`` dict is populated inside the test endpoint
    so the test body can assert on the request-time state seen by
    downstream handlers (``request.state.request_id``, contextvars,
    OTel current span).
    """
    test_app = fastapi.FastAPI()
    test_app.add_middleware(middleware_mod.RequestIdMiddleware)

    @test_app.get("/probe")
    def _probe(request: fastapi.Request) -> dict[str, str]:
        if capture is not None:
            capture["request_state"] = request.state.request_id
            capture["contextvars"] = dict(structlog.contextvars.get_contextvars())
            current_span = otel_trace.get_current_span()
            capture["span"] = current_span
        return {"status": "ok"}

    return test_app


@pytest.fixture
def _isolated_contextvars():
    """Clear structlog contextvars before and after each test."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def recorded_spans():
    """Install an in-memory span exporter and yield the exporter."""
    # Given a fresh TracerProvider with an in-memory exporter installed
    # via the package-private slot. Using ``set_tracer_provider`` is a
    # one-way door (the proxy refuses re-init), so we manipulate the
    # module attribute directly and clear it on teardown so other tests
    # see no provider, mirroring the package's pre-test default.
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    previous = otel_trace._TRACER_PROVIDER  # type: ignore[attr-defined]
    otel_trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    otel_trace._TRACER_PROVIDER_SET_ONCE._done = True  # type: ignore[attr-defined]

    try:
        yield exporter
    finally:
        otel_trace._TRACER_PROVIDER = previous  # type: ignore[attr-defined]


@pytest.mark.usefixtures("_isolated_contextvars")
class TestRequestIdMiddlewareHeaderHandling:
    """Tests for ``X-Request-Id`` header parsing and minting."""

    def test_mints_uuid4_when_header_absent(self):
        # Given a request without an X-Request-Id header
        app = _build_test_app()
        client = TestClient(app)

        # When the endpoint is invoked
        response = client.get("/probe")

        # Then the response carries a freshly-minted UUID4 in the header
        assert response.status_code == 200
        minted = response.headers["X-Request-Id"]
        parsed = uuid.UUID(minted)
        assert parsed.version == 4

    def test_echoes_back_valid_incoming_uuid(self):
        # Given a request with a syntactically-valid X-Request-Id header
        app = _build_test_app()
        client = TestClient(app)

        # When the endpoint is invoked with that header
        response = client.get("/probe", headers={"X-Request-Id": _VALID_UUID})

        # Then the same id is echoed in the response header
        assert response.status_code == 200
        assert response.headers["X-Request-Id"] == _VALID_UUID

    def test_mints_fresh_uuid_when_header_malformed(self):
        # Given a request with a malformed X-Request-Id header
        app = _build_test_app()
        client = TestClient(app)

        # When the endpoint is invoked
        response = client.get("/probe", headers={"X-Request-Id": "not-a-uuid"})

        # Then the malformed value is not echoed; a fresh UUID4 is minted
        assert response.status_code == 200
        echoed = response.headers["X-Request-Id"]
        assert echoed != "not-a-uuid"
        parsed = uuid.UUID(echoed)
        assert parsed.version == 4

    def test_logs_warning_when_header_malformed(self):
        # Given a request with a malformed X-Request-Id header
        app = _build_test_app()
        client = TestClient(app)

        # When the endpoint is invoked with the patched event log
        with mock.patch.object(middleware_mod.logs, "log_event") as log_event_mock:
            client.get("/probe", headers={"X-Request-Id": "not-a-uuid"})

        # Then a structured warning event is emitted with the bad value
        log_event_mock.assert_any_call(
            "request_id_invalid",
            params={"received": "not-a-uuid"},
        )


@pytest.mark.usefixtures("_isolated_contextvars")
class TestRequestIdMiddlewareDownstreamPropagation:
    """Tests that the request_id is exposed to downstream handlers."""

    def test_sets_request_state_request_id_as_uuid(self):
        # Given a request with a valid X-Request-Id header
        captured: dict[str, Any] = {}
        app = _build_test_app(capture=captured)
        client = TestClient(app)

        # When the endpoint is invoked
        client.get("/probe", headers={"X-Request-Id": _VALID_UUID})

        # Then request.state.request_id is the parsed UUID object
        assert captured["request_state"] == uuid.UUID(_VALID_UUID)
        assert isinstance(captured["request_state"], uuid.UUID)

    def test_binds_request_id_to_structlog_contextvars(self):
        # Given a request with a valid X-Request-Id header
        captured: dict[str, Any] = {}
        app = _build_test_app(capture=captured)
        client = TestClient(app)

        # When the endpoint is invoked
        client.get("/probe", headers={"X-Request-Id": _VALID_UUID})

        # Then structlog contextvars carry request_id as a string
        assert captured["contextvars"]["request_id"] == _VALID_UUID

    def test_unbinds_contextvars_after_request(self):
        # Given a fresh app with cleared contextvars
        app = _build_test_app()
        client = TestClient(app)

        # When a request completes
        client.get("/probe", headers={"X-Request-Id": _VALID_UUID})

        # Then contextvars are cleared so a subsequent request does not
        # inherit the previous request's id
        assert "request_id" not in structlog.contextvars.get_contextvars()

    @_skip_when_otel_disabled
    def test_sets_request_id_attribute_on_current_otel_span(self, recorded_spans):
        # Given a real recording span installed for the request
        app = _build_test_app()
        tracer = otel_trace.get_tracer("test")

        @app.middleware("http")
        async def _wrap_in_span(request, call_next):
            # Open a recording span around the inner middleware so the
            # RequestIdMiddleware's set_attribute call lands somewhere
            # observable.
            with tracer.start_as_current_span("http.request"):
                return await call_next(request)

        client = TestClient(app)

        # When the endpoint is invoked with a known UUID
        client.get("/probe", headers={"X-Request-Id": _VALID_UUID})

        # Then the captured span carries request_id as an attribute
        finished = recorded_spans.get_finished_spans()
        assert finished, "expected at least one finished span"
        request_id_attrs = [
            span.attributes.get("request_id")
            for span in finished
            if span.attributes and "request_id" in span.attributes
        ]
        assert _VALID_UUID in request_id_attrs


@pytest.mark.usefixtures("_isolated_contextvars")
class TestRequestIdMiddlewareResponseHeader:
    """Tests for the response-header echo on every path."""

    def test_response_header_matches_minted_uuid(self):
        # Given a request without an inbound id
        captured: dict[str, Any] = {}
        app = _build_test_app(capture=captured)
        client = TestClient(app)

        # When the endpoint is invoked
        response = client.get("/probe")

        # Then the response header equals the str form of the UUID seen
        # by the downstream handler
        assert response.headers["X-Request-Id"] == str(captured["request_state"])

    def test_response_header_present_on_error_responses(self):
        # Given an app whose endpoint raises an HTTPException
        app = fastapi.FastAPI()
        app.add_middleware(middleware_mod.RequestIdMiddleware)

        @app.get("/boom")
        def _boom() -> None:
            raise fastapi.HTTPException(status_code=418)

        client = TestClient(app)

        # When the failing endpoint is invoked
        response = client.get("/boom", headers={"X-Request-Id": _VALID_UUID})

        # Then the response still carries the request id header
        assert response.status_code == 418
        assert response.headers["X-Request-Id"] == _VALID_UUID


class TestRequestIdMiddlewareWiring:
    """Tests that the middleware is registered on the production app."""

    def test_main_app_health_check_echoes_request_id(self, patch_settings):
        # Given the production FastAPI app with the DB and LangGraph
        # checkpointer lifespan steps stubbed so the test does not need
        # a live database
        fake = patch_settings(api_app)
        fake.database_url = "fake"
        fake.otel_metrics_enabled = False
        fake.otel_service_name = "sentinel-test"
        saver_close = mock.AsyncMock()
        with (
            mock.patch("sentinel.interfaces.api.app.async_db.connect_db"),
            mock.patch("sentinel.interfaces.api.app.async_db.disconnect_db"),
            mock.patch("sentinel.interfaces.api.app.database.get_engine"),
            mock.patch("sentinel.interfaces.api.app.database.close_engine"),
            mock.patch("sentinel.interfaces.api.app.bootstrap_otel.instrument_sqlalchemy"),
            mock.patch(
                "sentinel.interfaces.api.app.workflows_checkpointer.build_checkpointer",
                new=mock.AsyncMock(return_value=(mock.MagicMock(), saver_close)),
            ),
            mock.patch(
                "sentinel.interfaces.api.app.workflows_support_review.build_support_review_graph"
            ),
            TestClient(api_app.app) as client,
        ):
            # When /health is hit with an X-Request-Id header
            response = client.get("/health", headers={"X-Request-Id": _VALID_UUID})

        # Then the same id is echoed in the response header
        assert response.status_code == 200
        assert response.headers["X-Request-Id"] == _VALID_UUID
