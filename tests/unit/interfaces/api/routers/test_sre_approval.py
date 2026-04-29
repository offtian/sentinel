"""
Unit tests for the SRE investigation approval endpoints (T33, T35).

Three endpoints under ``/api/sre/investigations/{investigation_id}/``:

- ``POST .../approve``          — resume the LangGraph SRE workflow with APPROVED
- ``POST .../reject``           — resume the LangGraph SRE workflow with REJECTED
- ``GET  .../approval-status``  — read the thread's checkpoint snapshot

Design:
- When ``app.state.sre_investigation_graph`` is set and
  ``get_investigation_status`` returns a pending status, the approve/reject
  endpoints call ``resume_investigation`` and return 200.
- When ``get_investigation_status`` returns ``None`` (no checkpoint), the
  endpoints fall back to the legacy ``_pending_approvals`` in-memory dict.
- When no graph is wired *and* no legacy entry exists, the endpoints
  return 404.

T35 — manual trigger:
- ``POST /api/sre/investigate`` always returns 202 regardless of the
  ``langgraph_sre_enabled`` flag; the enqueue logic is unchanged.
"""

from __future__ import annotations

import uuid
from unittest import mock

import fastapi
import pytest
from fastapi.testclient import TestClient

from sentinel.domain.approval import entities as approval_entities
from sentinel.interfaces.api import middleware as middleware_mod
from sentinel.interfaces.api.routers.sre import router as sre_router_mod
from sentinel.interfaces.workflows import sre_investigation as workflows_sre_investigation


_INVESTIGATION_UUID = uuid.UUID("aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb")
_INVESTIGATION_ID = str(_INVESTIGATION_UUID)


def _build_app(*, graph: mock.MagicMock | None) -> fastapi.FastAPI:
    """Wire only the SRE router with ``graph`` on app.state."""
    app = fastapi.FastAPI()
    app.add_middleware(middleware_mod.RequestIdMiddleware)
    app.include_router(sre_router_mod.router, prefix="/api")
    app.state.sre_investigation_graph = graph
    return app


def _pending_status() -> workflows_sre_investigation.InvestigationStatus:
    return workflows_sre_investigation.InvestigationStatus(
        request_id=_INVESTIGATION_UUID,
        status="pending",
        needs_approval=True,
        approval_decision=None,
    )


def _patch_status(monkeypatch: pytest.MonkeyPatch, status: object) -> None:
    async def fake_status(*, request_id: uuid.UUID, graph: object) -> object:
        return status

    monkeypatch.setattr(
        sre_router_mod.workflows_sre_investigation,
        "get_investigation_status",
        fake_status,
    )


def _patch_resume(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    async def fake_resume(
        *,
        request_id: uuid.UUID,
        decision: approval_entities.ApprovalDecision,
        graph: object,
        approver: str | None = None,
        reason: str | None = None,
    ) -> workflows_sre_investigation.InvestigationOutcome:
        captured["resume"] = {
            "request_id": request_id,
            "decision": decision,
            "graph": graph,
            "approver": approver,
            "reason": reason,
        }
        return workflows_sre_investigation.InvestigationOutcome(
            request_id=request_id,
            classification_category="infra",
            root_cause="disk full",
            remediation="extend volume",
            confidence=None,
            needs_approval=False,
            findings_published=True,
            interrupt_payload=None,
            approval_decision=decision,
        )

    monkeypatch.setattr(
        sre_router_mod.workflows_sre_investigation,
        "resume_investigation",
        fake_resume,
    )


@pytest.fixture
def captured() -> dict:
    return {}


# ---------------------------------------------------------------------------
# Helpers — isolate the legacy _pending_approvals dict between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_pending_approvals() -> None:
    """Ensure the module-level _pending_approvals dict is empty before each test."""
    sre_router_mod._pending_approvals.clear()
    yield
    sre_router_mod._pending_approvals.clear()


# ---------------------------------------------------------------------------
# T33 — Approve endpoint
# ---------------------------------------------------------------------------


class TestApproveEndpoint:
    def test_approve_uses_langgraph_when_checkpoint_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        captured: dict,
    ) -> None:
        # Given a thread paused at the approval gate (LangGraph checkpoint)
        graph_stub = mock.MagicMock(name="SREGraph")
        _patch_status(monkeypatch, _pending_status())
        _patch_resume(monkeypatch, captured)
        client = TestClient(_build_app(graph=graph_stub))

        # When an engineer POSTs the approve endpoint
        response = client.post(
            f"/api/sre/investigations/{_INVESTIGATION_ID}/approve",
            json={"reviewer": "alice@example.com"},
        )

        # Then the LangGraph workflow resumes with APPROVED and 200 is returned
        assert response.status_code == 200
        body = response.json()
        assert body["investigation_id"] == _INVESTIGATION_ID
        assert body["status"] == "approved"
        assert body["reviewer"] == "alice@example.com"
        assert "approved_at" in body

        # And resume_investigation received the right decision
        recorded = captured["resume"]
        assert recorded["request_id"] == _INVESTIGATION_UUID
        assert recorded["decision"] is approval_entities.ApprovalDecision.APPROVED
        assert recorded["graph"] is graph_stub
        assert recorded["approver"] == "alice@example.com"

    def test_reject_uses_langgraph_when_checkpoint_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        captured: dict,
    ) -> None:
        # Given a thread paused at the approval gate
        graph_stub = mock.MagicMock(name="SREGraph")
        _patch_status(monkeypatch, _pending_status())
        _patch_resume(monkeypatch, captured)
        client = TestClient(_build_app(graph=graph_stub))

        # When an engineer POSTs the reject endpoint
        response = client.post(
            f"/api/sre/investigations/{_INVESTIGATION_ID}/reject",
            json={"reviewer": "bob@example.com"},
        )

        # Then the LangGraph workflow resumes with REJECTED and 200 is returned
        assert response.status_code == 200
        body = response.json()
        assert body["investigation_id"] == _INVESTIGATION_ID
        assert body["status"] == "rejected"
        assert body["reviewer"] == "bob@example.com"
        assert "rejected_at" in body

        # And resume_investigation received the REJECTED decision
        recorded = captured["resume"]
        assert recorded["decision"] is approval_entities.ApprovalDecision.REJECTED

    def test_approve_falls_back_to_legacy_when_no_checkpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        captured: dict,
    ) -> None:
        # Given a graph is wired but get_investigation_status returns None
        # (investigation wasn't run via LangGraph) and a legacy entry exists
        _patch_status(monkeypatch, None)

        async def should_not_be_called(**kwargs):  # pragma: no cover
            captured["resume"] = kwargs

        monkeypatch.setattr(
            sre_router_mod.workflows_sre_investigation,
            "resume_investigation",
            should_not_be_called,
        )
        graph_stub = mock.MagicMock(name="SREGraph")
        client = TestClient(_build_app(graph=graph_stub))
        # Seed a legacy pending approval
        sre_router_mod.store_pending_approval(
            investigation_id=_INVESTIGATION_ID,
            approval_data={"alert_id": "alert-123"},
        )

        # When the approve endpoint is called
        response = client.post(
            f"/api/sre/investigations/{_INVESTIGATION_ID}/approve",
            json={"reviewer": "carol@example.com"},
        )

        # Then the legacy path handles it with 200 and resume was not called
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["reviewer"] == "carol@example.com"
        assert "resume" not in captured

    def test_approve_returns_404_when_no_graph_and_no_legacy(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given no graph wired and no legacy pending approval
        client = TestClient(_build_app(graph=None))

        # When the approve endpoint is called
        response = client.post(
            f"/api/sre/investigations/{_INVESTIGATION_ID}/approve",
            json={"reviewer": "dave@example.com"},
        )

        # Then 404 is returned because the investigation is unknown
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert body["investigation_id"] == _INVESTIGATION_ID

    def test_reject_returns_404_when_no_graph_and_no_legacy(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given no graph wired and no legacy pending approval
        client = TestClient(_build_app(graph=None))

        # When the reject endpoint is called
        response = client.post(
            f"/api/sre/investigations/{_INVESTIGATION_ID}/reject",
            json={"reviewer": "eve@example.com"},
        )

        # Then 404 is returned
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# T33 — approval-status endpoint
# ---------------------------------------------------------------------------


class TestApprovalStatusEndpoint:
    def test_approval_status_uses_langgraph_when_checkpoint_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given a thread paused at the approval gate
        _patch_status(monkeypatch, _pending_status())
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When the approval-status endpoint is called
        response = client.get(f"/api/sre/investigations/{_INVESTIGATION_ID}/approval-status")

        # Then the checkpoint snapshot is surfaced as 200
        assert response.status_code == 200
        body = response.json()
        assert body["investigation_id"] == _INVESTIGATION_ID
        assert body["status"] == "pending"

    def test_approval_status_falls_back_to_legacy(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given no checkpoint for the investigation but a legacy entry exists
        _patch_status(monkeypatch, None)
        client = TestClient(_build_app(graph=mock.MagicMock()))
        sre_router_mod.store_pending_approval(
            investigation_id=_INVESTIGATION_ID,
            approval_data={"alert_id": "alert-456"},
        )

        # When the approval-status endpoint is called
        response = client.get(f"/api/sre/investigations/{_INVESTIGATION_ID}/approval-status")

        # Then the legacy pending data is returned
        assert response.status_code == 200
        body = response.json()
        assert body["investigation_id"] == _INVESTIGATION_ID
        assert body["status"] == "pending"
        assert "requested_at" in body

    def test_approval_status_returns_404_when_no_graph_and_no_legacy(self) -> None:
        # Given no graph wired and no legacy entry
        client = TestClient(_build_app(graph=None))

        # When the approval-status endpoint is called
        response = client.get(f"/api/sre/investigations/{_INVESTIGATION_ID}/approval-status")

        # Then 404 is returned
        assert response.status_code == 404
        body = response.json()
        assert "error" in body

    def test_approval_status_returns_404_when_thread_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given a graph is wired but no checkpoint exists for this ID
        # and no legacy entry
        _patch_status(monkeypatch, None)
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When the approval-status endpoint is called
        response = client.get(f"/api/sre/investigations/{_INVESTIGATION_ID}/approval-status")

        # Then 404 is returned
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# T35 — manual trigger always returns 202
# ---------------------------------------------------------------------------


class TestManualTriggerEndpoint:
    def test_investigate_returns_202_regardless_of_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the manual trigger endpoint with the SRE database dependency mocked
        # (we only care that it enqueues and returns 202, not the flag routing)
        from sentinel.data import db as async_db_mod
        from sentinel.domain.jobs import operations as job_ops_mod

        graph_stub = mock.MagicMock(name="SREGraph")
        app = _build_app(graph=graph_stub)

        job_id = uuid.uuid4()
        monkeypatch.setattr(
            job_ops_mod,
            "enqueue_investigation",
            mock.AsyncMock(return_value=job_id),
        )
        monkeypatch.setattr(
            async_db_mod,
            "get_db",
            mock.MagicMock(return_value=mock.MagicMock()),
        )

        client = TestClient(app)

        # When the manual trigger is called
        response = client.post(
            "/api/sre/investigate",
            json={
                "id": "alert-test-001",
                "title": "High CPU",
                "description": "CPU at 95%",
                "severity": "high",
                "service": "api-service",
            },
        )

        # Then 202 Accepted is returned (same as before, flag has no effect here)
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "accepted"
        assert "job_id" in body
