"""
Unit tests for envelope wiring in the support router (F2.4).

Webhook handlers (Jira) and the manual ``/review`` endpoint must
construct an ``Envelope`` from the inbound payload, derive ``tenant_id``
from the project key, and pass it into the enqueue path so the worker
sees the same identity context.
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
from sentinel.interfaces.api.routers.support import router as support_router_mod


_VALID_REQUEST_ID = "12345678-1234-5678-1234-567812345678"


def _build_app() -> fastapi.FastAPI:
    """Wire only the support router and the request-id middleware."""
    app = fastapi.FastAPI()
    app.add_middleware(middleware_mod.RequestIdMiddleware)
    app.include_router(support_router_mod.router, prefix="/api")
    return app


def _build_settings_stub(
    *,
    cluster_name: str = "prod-eu-west-1",
    region: str = "eu-west-1",
) -> mock.MagicMock:
    settings_stub = mock.MagicMock()
    settings_stub.support_auto_draft = True
    settings_stub.k8s_cluster_name = cluster_name
    settings_stub.region = region
    return settings_stub


def _build_config_stub(*, strict: bool = False) -> mock.MagicMock:
    config_stub = mock.MagicMock()
    config_stub.envelope_strict_mode = strict
    return config_stub


@pytest.fixture
def captured_enqueue() -> dict[str, Any]:
    return {}


@pytest.fixture
def patched_router(monkeypatch, captured_enqueue):
    """Patch _enqueue_ticket plus get_settings/get_config in soft-fail mode."""
    fake_job_id = uuid.uuid4()

    async def fake_enqueue(ticket, *, requested_by, priority=2, envelope=None):
        captured_enqueue["ticket"] = ticket
        captured_enqueue["envelope"] = envelope
        return fastapi.responses.JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "job_id": str(fake_job_id),
                "ticket_key": ticket.key,
            },
        )

    monkeypatch.setattr(support_router_mod, "_enqueue_ticket", fake_enqueue)
    monkeypatch.setattr(support_router_mod, "get_settings", lambda: _build_settings_stub())
    monkeypatch.setattr(support_router_mod, "get_config", lambda: _build_config_stub(strict=False))


# ---------------------------------------------------------------------------
# Jira webhook
# ---------------------------------------------------------------------------


class TestJiraWebhookEnvelopeWiring:
    """Tests that the Jira webhook routes envelope identity into the queue."""

    def test_passes_envelope_with_project_tenant_to_enqueue(
        self, patched_router, captured_enqueue
    ):
        # Given a Jira webhook payload carrying a project key
        client = TestClient(_build_app())
        payload = {
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

        # When the webhook is invoked
        response = client.post(
            "/api/support/webhooks/jira",
            json=payload,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the envelope tenant_id is the lowercased project key
        assert response.status_code == 202
        envelope = captured_enqueue["envelope"]
        assert envelope is not None
        assert envelope.tenant_id == "support"
        assert str(envelope.request_id) == _VALID_REQUEST_ID

    def test_falls_back_to_unknown_when_no_project_key(self, patched_router, captured_enqueue):
        # Given a Jira payload with no project field
        client = TestClient(_build_app())
        payload = {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "id": "201",
                "key": "X-1",
                "fields": {"summary": "x", "description": "y"},
            },
        }

        # When the webhook is invoked
        response = client.post(
            "/api/support/webhooks/jira",
            json=payload,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the enqueue still happens with tenant_id="unknown"
        assert response.status_code == 202
        envelope = captured_enqueue["envelope"]
        assert envelope is not None
        assert envelope.tenant_id == "unknown"


# ---------------------------------------------------------------------------
# Manual /review endpoint
# ---------------------------------------------------------------------------


class TestManualReviewEnvelopeWiring:
    """Tests for envelope wiring in the manual /review endpoint."""

    def test_uses_sentinel_manual_tenant_id_when_unspecified(
        self, patched_router, captured_enqueue
    ):
        # Given a manual review request without tenant identifiers
        client = TestClient(_build_app())
        payload = {
            "id": "manual-1",
            "key": "MANUAL-1",
            "summary": "On demand review",
            "description": "Help me",
            "reporter": "Alice",
            "priority": "Medium",
        }

        # When the manual endpoint is invoked
        response = client.post(
            "/api/support/review",
            json=payload,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the envelope tenant_id is the "manual" sentinel
        assert response.status_code == 202
        envelope = captured_enqueue["envelope"]
        assert envelope is not None
        assert envelope.tenant_id == "manual"


# ---------------------------------------------------------------------------
# Strict mode behaviour
# ---------------------------------------------------------------------------


class TestJiraWebhookStrictMode:
    """Tests that strict mode hard-fails when tenant_id cannot be derived."""

    def test_returns_422_when_strict_mode_enabled_and_tenant_unknown(self, monkeypatch):
        # Given strict mode is on and the payload has no project key
        monkeypatch.setattr(
            support_router_mod,
            "get_settings",
            lambda: _build_settings_stub(cluster_name="", region=""),
        )
        monkeypatch.setattr(
            support_router_mod, "get_config", lambda: _build_config_stub(strict=True)
        )
        client = TestClient(_build_app())
        payload = {
            "webhookEvent": "jira:issue_created",
            "issue": {"id": "999", "key": "X-1", "fields": {}},
        }

        # When the webhook is invoked
        response = client.post(
            "/api/support/webhooks/jira",
            json=payload,
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the request is rejected with 422 Unprocessable Entity
        assert response.status_code == 422
        body = json.loads(response.content)
        assert body["error"] == "envelope_ingress_missing_tenant_id"
        assert body["source"] == "jira"
