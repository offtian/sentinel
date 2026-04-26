"""
End-to-end integration tests for ``request_id`` and envelope propagation (F2.9).

Closes Phase F2 by exercising the full ingress chain in one process:

    POST /api/<source>/webhooks/<vendor>
        -> RequestIdMiddleware mints/echoes ``X-Request-Id``
        -> webhook handler builds an Envelope via ``envelope_factory``
        -> response carries ``X-Request-Id`` back to the caller
        -> the captured envelope drives a synchronous pipeline run
        -> the pipeline node sets the six envelope-owned OTel span attributes
        -> ``run_node_with_envelope`` binds the full envelope log-context

The webhook handlers in production enqueue a job rather than driving the
pipeline inline — the pipeline runs in a separate worker process. To
exercise F2.6 (span attrs) and F2.7 (structlog log-context binding) from
a webhook test, the ``patched_router`` fixture stubs ``_enqueue_alert`` /
``_enqueue_ticket`` to drive ``investigate_alert`` / ``review_ticket``
synchronously with the captured envelope and a fake-agent config drawn
from ``tests.functional.conftest``. This proves the end-to-end identity
chain even though the production deployment is async.

Deferred test cases (depend on later F-phases):

- **F3 — DB row carries ``ingress_request_id``**: once the worker
  persists ``request_id`` onto the investigation / review row, add a
  case asserting the row's ``ingress_request_id`` matches the response
  header. F3 lands the schema migration that introduces this column;
  the worker already merges the envelope identity into the queued
  payload via ``_envelope_payload`` (see SRE / support routers).
- **F4 — Langfuse span export**: once spans flow into Langfuse, add a
  case that captures the exported spans and asserts the same six
  envelope attributes survive the export boundary. Today only the
  in-process exporter is asserted on.

The third deferred check (cross-process worker run) is intentionally
out of scope for F2 because it would require a live job queue and a
running worker — F3 will introduce a deterministic worker harness.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest import mock

import fastapi
import pytest
import structlog
from fastapi.testclient import TestClient
from logfire._internal.config import GLOBAL_CONFIG as _LOGFIRE_GLOBAL_CONFIG
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.interfaces.api import middleware as middleware_mod
from sentinel.interfaces.api.routers.sre import router as sre_router_mod
from sentinel.interfaces.api.routers.support import router as support_router_mod
from sentinel.interfaces.graphs import investigation, support_review
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    response_drafter,
    root_cause_analyser,
    ticket_reviewer,
)
from sentinel.interfaces.workflows import support_review as workflows_support_review
from tests import factories
from tests.functional.conftest import (
    EmptyDocumentSearcher,
    EmptyPastTicketSearcher,
    FakeAgentResult,
    _build_fake_config,
    _make_fake_agent,
)


# ---------------------------------------------------------------------------
# Fixtures: contextvar isolation and span exporter
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_contextvars():
    """Clear structlog contextvars before and after each test."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def recorded_spans():
    """
    Install an in-memory span exporter and yield it for the test body.

    Mirrors the pattern in ``tests/unit/interfaces/api/test_middleware.py``:
    ``otel_trace.set_tracer_provider`` is a one-way door, so the fixture
    pokes ``otel_trace._TRACER_PROVIDER`` directly and restores the
    previous provider on teardown so other tests are not affected.

    Also redirects logfire's ``ProxyTracerProvider`` to delegate to our
    ``TracerProvider``. PydanticGraph wraps every node in a
    ``logfire.span(...)`` context, which makes the logfire-managed span
    the current OTel span at the moment ``instrumented_node_run`` calls
    ``otel_trace.get_current_span().set_attributes(...)``. Without this
    redirect the call lands on a ``NoOpSpan`` and the envelope attrs
    silently drop. ``logfire.GLOBAL_CONFIG.get_tracer_provider()``
    returns the ProxyTracerProvider; ``set_provider`` swaps the inner
    provider so existing ``_ProxyTracer`` instances pick up our
    recording provider.
    """
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    previous = otel_trace._TRACER_PROVIDER  # type: ignore[attr-defined]
    otel_trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    otel_trace._TRACER_PROVIDER_SET_ONCE._done = True  # type: ignore[attr-defined]

    logfire_proxy = _LOGFIRE_GLOBAL_CONFIG.get_tracer_provider()
    previous_logfire_provider = logfire_proxy.provider
    logfire_proxy.set_provider(provider)

    try:
        yield exporter
    finally:
        logfire_proxy.set_provider(previous_logfire_provider)
        otel_trace._TRACER_PROVIDER = previous  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers: app construction and pipeline-driven enqueue stubs
# ---------------------------------------------------------------------------


def _build_settings_stub(
    *,
    sre_auto_investigate: bool = True,
    support_auto_draft: bool = True,
    cluster_name: str = "prod-eu-west-1",
    region: str = "eu-west-1",
) -> mock.MagicMock:
    """Return a Settings stub used by the SRE and support routers."""
    settings_stub = mock.MagicMock()
    settings_stub.sre_auto_investigate = sre_auto_investigate
    settings_stub.support_auto_draft = support_auto_draft
    settings_stub.k8s_cluster_name = cluster_name
    settings_stub.region = region
    return settings_stub


def _build_config_stub(*, strict: bool = False) -> mock.MagicMock:
    """Return a config stub exposing only ``envelope_strict_mode``."""
    config_stub = mock.MagicMock()
    config_stub.envelope_strict_mode = strict
    return config_stub


def _build_sre_only_app() -> fastapi.FastAPI:
    """Return a minimal app wiring just the middleware and SRE router."""
    app = fastapi.FastAPI()
    app.add_middleware(middleware_mod.RequestIdMiddleware)
    app.include_router(sre_router_mod.router, prefix="/api")
    return app


def _build_support_only_app(
    *,
    graph: object | None = None,
) -> fastapi.FastAPI:
    """Return a minimal app wiring just the middleware and support router.

    The post-T17 webhook reads the compiled graph off
    ``app.state.support_review_graph``; tests pass a sentinel object
    here to satisfy the not-None check while leaving the actual graph
    invocation to the patched entrypoint fixture.
    """
    app = fastapi.FastAPI()
    app.add_middleware(middleware_mod.RequestIdMiddleware)
    app.include_router(support_router_mod.router, prefix="/api")
    app.state.support_review_graph = graph if graph is not None else mock.MagicMock()
    return app


async def _fake_root_cause_run(*, user_prompt, deps, **kwargs):
    """Deterministic root cause analysis with high confidence."""
    return FakeAgentResult(
        root_cause_analyser.RootCauseAnalysis(
            root_cause="OOMKill",
            confidence=0.85,
            evidence=["evidence"],
            remediation_steps=["increase memory"],
            affected_services=["api-service"],
            timeline="now",
        )
    )


async def _fake_response_drafter_run(*, user_prompt, deps, **kwargs):
    """Deterministic response drafter."""
    return FakeAgentResult(
        response_drafter.DraftedResponse(
            response="Hi",
            sources_used=[
                response_drafter.SourceReference(
                    title="Doc",
                    url="https://example.com",
                ),
            ],
            confidence=0.82,
            notes_for_agent="ok",
        )
    )


def _build_sre_capturing_classifier(
    captured: dict[str, Any],
) -> Any:
    """
    Return a fake classifier that captures structlog contextvars on call.

    Captures the bound contextvars at the moment the agent runs so the
    test can assert on the envelope log-context binding made by
    ``run_node_with_envelope``.
    """

    async def _spy(*, user_prompt, deps, **kwargs):
        captured["sre_contextvars"] = dict(structlog.contextvars.get_contextvars())
        return FakeAgentResult(
            alert_classifier.AlertClassification(
                severity="high",
                affected_service="api-service",
                category="infrastructure",
                summary="Pod OOMKilled",
                requires_immediate_action=True,
            )
        )

    return _make_fake_agent(_spy)


def _build_support_capturing_reviewer(
    captured: dict[str, Any],
) -> Any:
    """
    Return a fake ticket reviewer that captures structlog contextvars.
    """

    async def _spy(*, user_prompt, deps, **kwargs):
        captured["support_contextvars"] = dict(structlog.contextvars.get_contextvars())
        return FakeAgentResult(
            ticket_reviewer.TicketClassification(
                category="account",
                urgency="high",
                required_expertise=["authentication"],
                key_questions=["q?"],
                search_queries=["q"],
            )
        )

    return _make_fake_agent(_spy)


@pytest.fixture
def captured_run() -> dict[str, Any]:
    """
    Return the dict the patched routers populate during a pipeline-backed run.

    Carries the envelope, the synchronous pipeline reply (or exception),
    and the structlog contextvars seen at the agent boundary so tests can
    inspect both the ingress envelope and the in-pipeline log context.
    """
    return {}


@pytest.fixture
def patched_sre_router(monkeypatch, captured_run):
    """
    Patch the SRE router's enqueue / settings / config to drive the pipeline.

    Replaces ``_enqueue_alert`` with a stub that captures the envelope and
    synchronously drives ``investigate_alert`` using a fake-agent config —
    this exercises the F2.6/F2.7 pipeline-side envelope propagation in
    the same process as the webhook request, which the production async
    deployment splits across the worker.
    """

    async def fake_enqueue(alert, *, requested_by, priority=1, envelope=None):
        captured_run["envelope"] = envelope
        captured_run["alert"] = alert

        capturing_classifier = _build_sre_capturing_classifier(captured_run)
        config_stub = _build_fake_config(
            {
                "alert_classifier": capturing_classifier,
                "root_cause_analyser": _make_fake_agent(_fake_root_cause_run),
            }
        )

        # Open a recording span around the pipeline run so the
        # ``instrumented_node_run`` calls land on a recording span that
        # the in-memory exporter can capture. In production, the
        # FastAPI/PydanticAI instrumentation creates spans for every
        # request and node; this test stub plays that role.
        tracer = otel_trace.get_tracer("sentinel.test.integration")
        with tracer.start_as_current_span("sre.pipeline"):
            captured_run["sre_reply"] = await investigation.investigate_alert(
                alert=alert,
                envelope=envelope,
                agent_for=config_stub.agent_for,
                holmes=factories.MockHolmesAdapter(),
                post_to_slack=False,
            )

        return fastapi.responses.JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "job_id": str(uuid.uuid4()),
                "alert_id": alert.id,
            },
        )

    monkeypatch.setattr(sre_router_mod, "_enqueue_alert", fake_enqueue)
    monkeypatch.setattr(sre_router_mod, "get_settings", lambda: _build_settings_stub())
    monkeypatch.setattr(sre_router_mod, "get_config", lambda: _build_config_stub(strict=False))


@pytest.fixture
def patched_support_router(monkeypatch, captured_run):
    """
    Patch the support router's synchronous review_ticket entrypoint to
    drive the pipeline in-process and capture the envelope.

    Post-T17 the support webhook reads the compiled graph off
    ``app.state.support_review_graph`` and calls
    ``workflows.support_review.review_ticket`` synchronously. This
    fixture replaces that entrypoint with a fake that captures the
    envelope and runs the legacy support pipeline (still serving
    request_id propagation coverage until the legacy module is moved
    to ``_archive/`` in T21).
    """

    async def fake_review_ticket(*, ticket, envelope, graph):
        captured_run["envelope"] = envelope
        captured_run["ticket"] = ticket

        capturing_reviewer = _build_support_capturing_reviewer(captured_run)
        config_stub = _build_fake_config(
            {
                "ticket_reviewer": capturing_reviewer,
                "response_drafter": _make_fake_agent(_fake_response_drafter_run),
            }
        )

        tracer = otel_trace.get_tracer("sentinel.test.integration")
        with tracer.start_as_current_span("support.pipeline"):
            captured_run["support_reply"] = await support_review.review_ticket(
                ticket=ticket,
                envelope=envelope,
                agent_for=config_stub.agent_for,
                document_searcher=EmptyDocumentSearcher(),
                ticket_searcher=EmptyPastTicketSearcher(),
            )

        return workflows_support_review.ReviewOutcome(
            request_id=envelope.request_id,
            response_suggestion=None,
            confidence=None,
            needs_approval=False,
            interrupt_payload=None,
            approval_decision=None,
        )

    async def fake_persist(**kwargs):
        return uuid.uuid4()

    monkeypatch.setattr(
        support_router_mod.workflows_support_review,
        "review_ticket",
        fake_review_ticket,
    )
    monkeypatch.setattr(support_router_mod.support_ops, "persist_ticket_review", fake_persist)
    monkeypatch.setattr(support_router_mod.async_db, "get_db", lambda: mock.MagicMock())
    monkeypatch.setattr(support_router_mod, "get_settings", lambda: _build_settings_stub())
    monkeypatch.setattr(support_router_mod, "get_config", lambda: _build_config_stub(strict=False))


# ---------------------------------------------------------------------------
# Webhook payload fixtures
# ---------------------------------------------------------------------------


def _pagerduty_payload() -> dict[str, Any]:
    """Return a minimal PagerDuty V3 payload with a derivable namespace."""
    return {
        "event": {
            "event_type": "incident.triggered",
            "data": {
                "id": "P777",
                "title": "Pod CrashLoop",
                "service": {"summary": "payments-api"},
                "body": {"details": {"namespace": "payments-prod"}},
            },
        },
    }


def _datadog_payload() -> dict[str, Any]:
    """Return a minimal Datadog payload with a derivable service tag."""
    return {
        "id": "DD123",
        "title": "[Triggered] High CPU on web-01",
        "body": "CPU usage exceeded threshold",
        "priority": "P1",
        "tags": "service:checkout-api,env:prod",
        "alert_transition": "Triggered",
    }


def _jira_payload() -> dict[str, Any]:
    """Return a minimal Jira webhook payload with a project key."""
    return {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "id": "200",
            "key": "SUPPORT-9",
            "fields": {
                "summary": "Cannot log in",
                "description": "I'm locked out",
                "project": {"key": "SUPPORT"},
                "reporter": {"displayName": "Jane"},
                "priority": {"name": "High"},
                "labels": [],
            },
        },
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_VALID_REQUEST_ID = "12345678-1234-5678-1234-567812345678"
_ENVELOPE_SPAN_KEYS = frozenset(
    {
        "request_id",
        "tenant_id",
        "cluster_id",
        "region",
        "pii_class",
        "received_at",
    }
)


def _envelope_log_context_keys(*, envelope: envelope_mod.Envelope) -> frozenset[str]:
    """Return the structlog binding keys produced by ``Envelope.to_log_context``."""
    return frozenset(envelope.to_log_context().keys())


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_isolated_contextvars")
class TestRequestIdEchoOnWebhook:
    """Cases 1, 2: header echo and minting on the SRE PagerDuty webhook."""

    def test_caller_supplied_request_id_is_echoed_in_response(
        self, patched_sre_router, captured_run
    ):
        # Given a caller-supplied X-Request-Id and a valid PagerDuty payload
        client = TestClient(_build_sre_only_app())

        # When the PagerDuty webhook is invoked
        response = client.post(
            "/api/sre/webhooks/pagerduty",
            json=_pagerduty_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the response header echoes the caller-supplied UUID
        assert response.status_code == 202
        assert response.headers["X-Request-Id"] == _VALID_REQUEST_ID
        # And the captured envelope reuses that same id
        assert str(captured_run["envelope"].request_id) == _VALID_REQUEST_ID

    def test_caller_omitted_request_id_is_minted_and_returned(
        self, patched_sre_router, captured_run
    ):
        # Given a request without an X-Request-Id header
        client = TestClient(_build_sre_only_app())

        # When the PagerDuty webhook is invoked
        response = client.post(
            "/api/sre/webhooks/pagerduty",
            json=_pagerduty_payload(),
        )

        # Then the response carries a freshly-minted UUID4
        assert response.status_code == 202
        echoed_id = response.headers["X-Request-Id"]
        parsed = uuid.UUID(echoed_id)
        assert parsed.version == 4
        # And the envelope's request_id matches what was echoed
        assert str(captured_run["envelope"].request_id) == echoed_id


@pytest.mark.usefixtures("_isolated_contextvars")
class TestEnvelopeSpanAttributes:
    """Case 3: envelope-derived attrs land on the SRE pipeline span."""

    def test_envelope_attrs_land_on_at_least_one_pipeline_span(
        self, recorded_spans, patched_sre_router, captured_run
    ):
        # Given a webhook flow that drives the SRE pipeline synchronously
        client = TestClient(_build_sre_only_app())

        # When the PagerDuty webhook is invoked
        client.post(
            "/api/sre/webhooks/pagerduty",
            json=_pagerduty_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then at least one finished span carries all six envelope attrs
        finished_spans = recorded_spans.get_finished_spans()
        assert finished_spans, "expected at least one finished span"

        envelope = captured_run["envelope"]
        spans_with_full_envelope = [
            span
            for span in finished_spans
            if span.attributes is not None
            and _ENVELOPE_SPAN_KEYS.issubset(set(span.attributes.keys()))
        ]
        assert spans_with_full_envelope, (
            f"expected span with all envelope keys; got "
            f"{[dict(s.attributes or {}) for s in finished_spans]}"
        )

        for span in spans_with_full_envelope:
            attrs = dict(span.attributes or {})
            assert attrs["request_id"] == str(envelope.request_id)
            assert attrs["tenant_id"] == envelope.tenant_id
            assert attrs["cluster_id"] == envelope.cluster_id
            assert attrs["region"] == envelope.region
            assert attrs["pii_class"] == envelope.pii_class
            assert attrs["received_at"] == envelope.received_at.isoformat()


@pytest.mark.usefixtures("_isolated_contextvars")
class TestEnvelopeStructlogContextBinding:
    """Case 4: structlog contextvars carry envelope fields during run."""

    def test_pipeline_node_binds_full_envelope_log_context(self, patched_sre_router, captured_run):
        # Given a webhook flow that drives the SRE pipeline synchronously
        client = TestClient(_build_sre_only_app())

        # When the PagerDuty webhook is invoked
        client.post(
            "/api/sre/webhooks/pagerduty",
            json=_pagerduty_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then structlog contextvars at the agent boundary carry all
        # envelope log-context keys (with tenant_id rendered straight, since
        # the default pii_class="internal" is not redacted)
        envelope = captured_run["envelope"]
        captured_contextvars = captured_run["sre_contextvars"]
        for key, value in envelope.to_log_context().items():
            assert captured_contextvars.get(key) == value

    def test_redacted_pii_class_emits_tenant_hash_instead_of_tenant_id(
        self, monkeypatch, captured_run
    ):
        # Given an SRE router patched to mint a confidential envelope
        # via a custom envelope_factory that elevates pii_class

        async def fake_enqueue(alert, *, requested_by, priority=1, envelope=None):
            # Replace the soft-mode envelope with one carrying a redacted
            # pii_class so we can verify ``Envelope.to_log_context``
            # substitutes ``tenant_hash`` for ``tenant_id``.
            elevated = envelope_mod.Envelope(
                request_id=envelope.request_id,
                tenant_id=envelope.tenant_id,
                cluster_id=envelope.cluster_id,
                region=envelope.region,
                pii_class="confidential",
                received_at=envelope.received_at,
            )
            captured_run["envelope"] = elevated
            captured_run["alert"] = alert

            capturing_classifier = _build_sre_capturing_classifier(captured_run)
            config_stub = _build_fake_config(
                {
                    "alert_classifier": capturing_classifier,
                    "root_cause_analyser": _make_fake_agent(_fake_root_cause_run),
                }
            )
            captured_run["sre_reply"] = await investigation.investigate_alert(
                alert=alert,
                envelope=elevated,
                agent_for=config_stub.agent_for,
                holmes=factories.MockHolmesAdapter(),
                post_to_slack=False,
            )
            return fastapi.responses.JSONResponse(
                status_code=202,
                content={
                    "status": "accepted",
                    "job_id": str(uuid.uuid4()),
                    "alert_id": alert.id,
                },
            )

        monkeypatch.setattr(sre_router_mod, "_enqueue_alert", fake_enqueue)
        monkeypatch.setattr(sre_router_mod, "get_settings", lambda: _build_settings_stub())
        monkeypatch.setattr(sre_router_mod, "get_config", lambda: _build_config_stub(strict=False))
        client = TestClient(_build_sre_only_app())

        # When the webhook drives a confidential-pii_class pipeline run
        client.post(
            "/api/sre/webhooks/pagerduty",
            json=_pagerduty_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the captured contextvars contain tenant_hash, not tenant_id
        captured_contextvars = captured_run["sre_contextvars"]
        assert "tenant_hash" in captured_contextvars
        assert "tenant_id" not in captured_contextvars


@pytest.mark.usefixtures("_isolated_contextvars")
class TestDatadogWebhookVariant:
    """Case 5: same chain via the Datadog webhook."""

    def test_datadog_webhook_propagates_request_id_and_envelope(
        self, recorded_spans, patched_sre_router, captured_run
    ):
        # Given a caller-supplied X-Request-Id and a Datadog payload
        client = TestClient(_build_sre_only_app())

        # When the Datadog webhook is invoked
        response = client.post(
            "/api/sre/webhooks/datadog",
            json=_datadog_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the response echoes the request id, the envelope carries it,
        # and the pipeline span captures all six envelope attrs
        assert response.status_code == 202
        assert response.headers["X-Request-Id"] == _VALID_REQUEST_ID
        envelope = captured_run["envelope"]
        assert str(envelope.request_id) == _VALID_REQUEST_ID
        assert envelope.tenant_id == "checkout-api"

        finished_spans = recorded_spans.get_finished_spans()
        spans_with_full_envelope = [
            span
            for span in finished_spans
            if span.attributes is not None
            and _ENVELOPE_SPAN_KEYS.issubset(set(span.attributes.keys()))
        ]
        assert spans_with_full_envelope


@pytest.mark.usefixtures("_isolated_contextvars")
class TestJiraSupportWebhookVariant:
    """Case 6: same chain via the Jira support webhook."""

    def test_jira_webhook_propagates_request_id_and_envelope(
        self, recorded_spans, patched_support_router, captured_run
    ):
        # Given a caller-supplied X-Request-Id and a Jira webhook payload
        client = TestClient(_build_support_only_app())

        # When the Jira webhook is invoked
        response = client.post(
            "/api/support/webhooks/jira",
            json=_jira_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the response echoes the request id, the envelope carries
        # the lowercased project key as tenant_id, and the support
        # pipeline span captures all six envelope attrs
        assert response.status_code == 200
        assert response.headers["X-Request-Id"] == _VALID_REQUEST_ID
        envelope = captured_run["envelope"]
        assert str(envelope.request_id) == _VALID_REQUEST_ID
        assert envelope.tenant_id == "support"

        finished_spans = recorded_spans.get_finished_spans()
        spans_with_full_envelope = [
            span
            for span in finished_spans
            if span.attributes is not None
            and _ENVELOPE_SPAN_KEYS.issubset(set(span.attributes.keys()))
        ]
        assert spans_with_full_envelope

        # And the support pipeline's structlog binding carried the full
        # envelope log-context at the agent boundary
        captured_contextvars = captured_run["support_contextvars"]
        for key in _envelope_log_context_keys(envelope=envelope):
            assert key in captured_contextvars


@pytest.mark.usefixtures("_isolated_contextvars")
class TestStrictModeRejection:
    """Case 7: strict mode rejects payloads missing tenant identifiers."""

    def test_strict_mode_rejects_missing_tenant_with_422(self, recorded_spans, monkeypatch):
        # Given strict mode is on, settings have no fallback cluster/region,
        # and the payload has no tenant identifiers
        monkeypatch.setattr(
            sre_router_mod,
            "get_settings",
            lambda: _build_settings_stub(cluster_name="", region=""),
        )
        monkeypatch.setattr(
            sre_router_mod,
            "get_config",
            lambda: _build_config_stub(strict=True),
        )

        # And a stub _enqueue_alert that should NEVER be invoked because
        # strict mode rejects the request before pipeline entry
        enqueue_was_called = {"called": False}

        async def fake_enqueue(*args, **kwargs):
            enqueue_was_called["called"] = True
            raise AssertionError("enqueue should not be called when strict mode rejects")

        monkeypatch.setattr(sre_router_mod, "_enqueue_alert", fake_enqueue)

        client = TestClient(_build_sre_only_app())
        payload_without_tenant = {
            "event": {
                "event_type": "incident.triggered",
                "data": {"id": "P000", "title": "Strict mystery"},
            },
        }

        # When the PagerDuty webhook is invoked
        response = client.post(
            "/api/sre/webhooks/pagerduty",
            json=payload_without_tenant,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the response is 422 with the documented envelope ingress error
        assert response.status_code == 422
        body = json.loads(response.content)
        assert body["error"] == "envelope_ingress_missing_tenant_id"
        assert body["source"] == "pagerduty"
        assert body["request_id"] == _VALID_REQUEST_ID

        # And the response still echoes the request id header
        assert response.headers["X-Request-Id"] == _VALID_REQUEST_ID

        # And no pipeline-node span was emitted because the request was
        # rejected before pipeline entry — only the middleware/inner spans
        # (none of which carry the full six envelope keys) should remain
        finished_spans = recorded_spans.get_finished_spans()
        spans_with_full_envelope = [
            span
            for span in finished_spans
            if span.attributes is not None
            and _ENVELOPE_SPAN_KEYS.issubset(set(span.attributes.keys()))
        ]
        assert not spans_with_full_envelope, (
            "no pipeline span should carry the full envelope when strict-mode rejects ingress"
        )
        assert not enqueue_was_called["called"]


@pytest.mark.usefixtures("_isolated_contextvars")
class TestSoftModeFallback:
    """Case 8: soft mode falls back to ``tenant_id="unknown"`` and warns."""

    def test_soft_mode_accepts_missing_tenant_with_unknown_fallback(
        self, monkeypatch, captured_run
    ):
        # Given soft (default) mode and a payload without tenant identifiers
        async def fake_enqueue(alert, *, requested_by, priority=1, envelope=None):
            captured_run["envelope"] = envelope
            return fastapi.responses.JSONResponse(
                status_code=202,
                content={
                    "status": "accepted",
                    "job_id": str(uuid.uuid4()),
                    "alert_id": alert.id,
                },
            )

        monkeypatch.setattr(sre_router_mod, "_enqueue_alert", fake_enqueue)
        monkeypatch.setattr(
            sre_router_mod,
            "get_settings",
            lambda: _build_settings_stub(cluster_name="", region=""),
        )
        monkeypatch.setattr(
            sre_router_mod,
            "get_config",
            lambda: _build_config_stub(strict=False),
        )

        # And a structured log capture so we can assert on the warning event
        with mock.patch(
            "sentinel.interfaces.webhooks.envelope_factory.logs.log_event"
        ) as log_event_mock:
            client = TestClient(_build_sre_only_app())
            payload_without_tenant = {
                "event": {
                    "event_type": "incident.triggered",
                    "data": {"id": "P000", "title": "Soft mystery"},
                },
            }

            # When the PagerDuty webhook is invoked
            response = client.post(
                "/api/sre/webhooks/pagerduty",
                json=payload_without_tenant,
                headers={"X-Request-Id": _VALID_REQUEST_ID},
            )

        # Then the request is accepted with tenant_id="unknown"
        assert response.status_code == 202
        envelope = captured_run["envelope"]
        assert envelope is not None
        assert envelope.tenant_id == "unknown"

        # And the canonical envelope_tenant_unknown structured event was
        # emitted so operators can spot the fallback
        unknown_calls = [
            call
            for call in log_event_mock.call_args_list
            if call.args and call.args[0] == "envelope_tenant_unknown"
        ]
        assert unknown_calls, "expected envelope_tenant_unknown warning to be emitted in soft mode"
        recorded_event = unknown_calls[0]
        params = recorded_event.kwargs.get("params") or {}
        assert params.get("source") == "pagerduty"
        assert params.get("request_id") == _VALID_REQUEST_ID
        assert params.get("fallback_used") is True
