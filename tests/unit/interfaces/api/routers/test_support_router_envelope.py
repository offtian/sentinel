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
from sentinel.interfaces.workflows import support_review as workflows_support_review
from tests import factories


_VALID_REQUEST_ID = "12345678-1234-5678-1234-567812345678"
_REQUEST_UUID = uuid.UUID(_VALID_REQUEST_ID)


def _build_app(*, graph: mock.MagicMock | None = None) -> fastapi.FastAPI:
    """Wire only the support router; optionally stash a graph on app.state."""
    app = fastapi.FastAPI()
    app.add_middleware(middleware_mod.RequestIdMiddleware)
    app.include_router(support_router_mod.router, prefix="/api")
    app.state.support_review_graph = graph
    return app


def _populate_settings(
    fake,
    *,
    cluster_name: str = "prod-eu-west-1",
    region: str = "eu-west-1",
) -> None:
    fake.support_auto_draft = True
    fake.k8s_cluster_name = cluster_name
    fake.region = region


def _build_config_stub(*, strict: bool = False) -> mock.MagicMock:
    config_stub = mock.MagicMock()
    config_stub.envelope_strict_mode = strict
    return config_stub


@pytest.fixture
def captured_enqueue() -> dict[str, Any]:
    return {}


@pytest.fixture
def patched_router(monkeypatch, captured_enqueue, patch_settings):
    """Patch the synchronous webhook entrypoint plus the manual queue path.

    The Jira webhook (post-T17) calls ``workflows.support_review.review_ticket``
    synchronously; the manual ``/review`` endpoint still rides the queue
    via ``_enqueue_ticket``. Both paths capture their inbound envelope
    onto ``captured_enqueue["envelope"]`` so the same fixture covers
    both endpoints' envelope wiring.
    """
    fake_job_id = uuid.uuid4()
    suggestion = factories.make_response_suggestion(ticket_id="200")
    confidence = factories.make_confidence_score(total=0.85)

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

    async def fake_review_ticket(*, ticket, envelope, graph):
        captured_enqueue["ticket"] = ticket
        captured_enqueue["envelope"] = envelope
        captured_enqueue["graph"] = graph
        return workflows_support_review.ReviewOutcome(
            request_id=envelope.request_id,
            response_suggestion=suggestion,
            confidence=confidence,
            needs_approval=False,
            interrupt_payload=None,
            approval_decision=None,
        )

    async def fake_persist(**kwargs):
        captured_enqueue["persist"] = kwargs
        return uuid.uuid4()

    monkeypatch.setattr(support_router_mod, "_enqueue_ticket", fake_enqueue)
    monkeypatch.setattr(
        support_router_mod.workflows_support_review,
        "review_ticket",
        fake_review_ticket,
    )
    monkeypatch.setattr(support_router_mod.support_ops, "persist_ticket_review", fake_persist)
    monkeypatch.setattr(support_router_mod.async_db, "get_db", lambda: mock.MagicMock())
    fake = patch_settings(support_router_mod)
    _populate_settings(fake)
    monkeypatch.setattr(support_router_mod, "get_config", lambda: _build_config_stub(strict=False))


# ---------------------------------------------------------------------------
# Jira webhook
# ---------------------------------------------------------------------------


class TestJiraWebhookEnvelopeWiring:
    """Tests that the Jira webhook routes envelope identity into the queue."""

    def test_passes_envelope_with_project_tenant_to_review_ticket(
        self, patched_router, captured_enqueue
    ):
        # Given a Jira webhook payload carrying a project key and a
        # graph stashed on app.state by the lifespan
        client = TestClient(_build_app(graph=mock.MagicMock(name="SupportGraph")))
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

        # Then the envelope passed into review_ticket carries the
        # lowercased project key as the tenant id
        assert response.status_code == 200
        envelope = captured_enqueue["envelope"]
        assert envelope is not None
        assert envelope.tenant_id == "support"
        assert str(envelope.request_id) == _VALID_REQUEST_ID

    def test_falls_back_to_unknown_when_no_project_key(self, patched_router, captured_enqueue):
        # Given a Jira payload with no project field
        client = TestClient(_build_app(graph=mock.MagicMock(name="SupportGraph")))
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

        # Then review_ticket still runs with tenant_id="unknown" rather
        # than the request being rejected
        assert response.status_code == 200
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

    def test_returns_422_when_strict_mode_enabled_and_tenant_unknown(
        self, monkeypatch, patch_settings
    ):
        # Given strict mode is on and the payload has no project key
        fake = patch_settings(support_router_mod)
        _populate_settings(fake, cluster_name="", region="")
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
