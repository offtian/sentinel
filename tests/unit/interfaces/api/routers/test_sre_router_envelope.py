"""
Unit tests for envelope wiring in the SRE router (F2.4).

Webhook handlers must:

- Read the middleware-minted ``request.state.request_id`` (UUID).
- Construct an ``Envelope`` via the appropriate ``envelope_from_*`` helper.
- Persist envelope identity onto the queued job payload so the worker
  rehydrates the same tenant context.

The manual ``/investigate`` endpoint also derives an envelope but uses
the sentinel ``tenant_id="manual"`` when none is supplied.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest import mock

import fastapi
import pytest
from fastapi.testclient import TestClient

from sentinel.interfaces.api import middleware as middleware_mod
from sentinel.interfaces.api.routers.sre import router as sre_router_mod


_VALID_REQUEST_ID = "12345678-1234-5678-1234-567812345678"


def _build_app() -> fastapi.FastAPI:
    """Wire only the SRE router and the request-id middleware."""
    app = fastapi.FastAPI()
    app.add_middleware(middleware_mod.RequestIdMiddleware)
    app.include_router(sre_router_mod.router, prefix="/api")
    return app


def _build_settings_stub(
    *,
    cluster_name: str = "prod-eu-west-1",
    region: str = "eu-west-1",
) -> mock.MagicMock:
    settings_stub = mock.MagicMock()
    settings_stub.sre_auto_investigate = True
    settings_stub.k8s_cluster_name = cluster_name
    settings_stub.region = region
    return settings_stub


def _build_config_stub(*, strict: bool = False) -> mock.MagicMock:
    config_stub = mock.MagicMock()
    config_stub.envelope_strict_mode = strict
    return config_stub


@pytest.fixture
def captured_enqueue() -> dict[str, Any]:
    """Return a captured enqueue dict populated by the patched _enqueue_alert."""
    return {}


@pytest.fixture
def patched_router(monkeypatch, captured_enqueue):
    """Patch _enqueue_alert plus get_settings/get_config in soft-fail mode."""
    fake_job_id = uuid.uuid4()

    async def fake_enqueue(alert, *, requested_by, priority=1, envelope=None):
        captured_enqueue["alert"] = alert
        captured_enqueue["requested_by"] = requested_by
        captured_enqueue["priority"] = priority
        captured_enqueue["envelope"] = envelope
        return fastapi.responses.JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "job_id": str(fake_job_id),
                "alert_id": alert.id,
            },
        )

    monkeypatch.setattr(sre_router_mod, "_enqueue_alert", fake_enqueue)
    monkeypatch.setattr(sre_router_mod, "get_settings", lambda: _build_settings_stub())
    monkeypatch.setattr(sre_router_mod, "get_config", lambda: _build_config_stub(strict=False))


# ---------------------------------------------------------------------------
# PagerDuty webhook
# ---------------------------------------------------------------------------


class TestPagerDutyWebhookEnvelopeWiring:
    """Tests that the PagerDuty webhook routes envelope identity into the queue."""

    def test_passes_envelope_with_namespace_tenant_to_enqueue(
        self, patched_router, captured_enqueue
    ):
        # Given a PagerDuty payload with a k8s namespace under body.details
        client = TestClient(_build_app())
        payload = {
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

        # When the webhook is invoked with a known X-Request-Id
        response = client.post(
            "/api/sre/webhooks/pagerduty",
            json=payload,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the enqueue call carries an envelope tagged to the namespace
        assert response.status_code == 202
        envelope = captured_enqueue["envelope"]
        assert envelope is not None
        assert envelope.tenant_id == "payments-prod"
        assert str(envelope.request_id) == _VALID_REQUEST_ID

    def test_falls_back_to_unknown_tenant_when_payload_lacks_identity(
        self, patched_router, captured_enqueue
    ):
        # Given a PagerDuty payload with no namespace and no service tag
        client = TestClient(_build_app())
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {"id": "P999", "title": "Mystery"},
            },
        }

        # When the webhook is invoked
        response = client.post(
            "/api/sre/webhooks/pagerduty",
            json=payload,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the enqueue still happens with tenant_id="unknown" (soft-fail)
        assert response.status_code == 202
        envelope = captured_enqueue["envelope"]
        assert envelope is not None
        assert envelope.tenant_id == "unknown"


# ---------------------------------------------------------------------------
# Datadog webhook
# ---------------------------------------------------------------------------


class TestDatadogWebhookEnvelopeWiring:
    """Tests that the Datadog webhook routes envelope identity into the queue."""

    def test_passes_envelope_with_service_tenant_to_enqueue(
        self, patched_router, captured_enqueue
    ):
        # Given a Datadog payload with a service tag
        client = TestClient(_build_app())
        payload = {
            "id": "DD123",
            "title": "[Triggered] High CPU on web-01",
            "body": "CPU usage exceeded threshold",
            "priority": "P1",
            "tags": "service:checkout-api,env:prod",
            "alert_transition": "Triggered",
        }

        # When the webhook is invoked
        response = client.post(
            "/api/sre/webhooks/datadog",
            json=payload,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the envelope's tenant_id is the service tag value
        assert response.status_code == 202
        envelope = captured_enqueue["envelope"]
        assert envelope is not None
        assert envelope.tenant_id == "checkout-api"


# ---------------------------------------------------------------------------
# Manual /investigate endpoint
# ---------------------------------------------------------------------------


class TestManualInvestigateEnvelopeWiring:
    """Tests for envelope wiring in the manual /investigate endpoint."""

    def test_uses_sentinel_manual_tenant_id_when_unspecified(
        self, patched_router, captured_enqueue
    ):
        # Given a manual investigation request without tenant identifiers
        client = TestClient(_build_app())
        payload = {
            "id": "manual-1",
            "title": "Manual",
            "description": "On-demand triage",
            "severity": "medium",
            "service": "myservice",
            "source": "manual",
        }

        # When the manual endpoint is invoked
        response = client.post(
            "/api/sre/investigate",
            json=payload,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the envelope tenant_id is the "manual" sentinel
        assert response.status_code == 202
        envelope = captured_enqueue["envelope"]
        assert envelope is not None
        assert envelope.tenant_id == "manual"


# ---------------------------------------------------------------------------
# Strict-mode behaviour — flag flipped via config
# ---------------------------------------------------------------------------


class TestPagerDutyWebhookStrictMode:
    """Tests that strict mode hard-fails when tenant_id cannot be derived."""

    def test_returns_422_when_strict_mode_enabled_and_tenant_unknown(self, monkeypatch):
        # Given strict mode is on and the payload has no tenant identifiers
        monkeypatch.setattr(
            sre_router_mod,
            "get_settings",
            lambda: _build_settings_stub(cluster_name="", region=""),
        )
        monkeypatch.setattr(sre_router_mod, "get_config", lambda: _build_config_stub(strict=True))
        client = TestClient(_build_app())
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {"id": "P000", "title": "Strict mystery"},
            },
        }

        # When the webhook is invoked
        response = client.post(
            "/api/sre/webhooks/pagerduty",
            json=payload,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the request is rejected with 422 Unprocessable Entity
        assert response.status_code == 422
        body = json.loads(response.content)
        assert body["error"] == "envelope_ingress_missing_tenant_id"
        assert body["source"] == "pagerduty"
