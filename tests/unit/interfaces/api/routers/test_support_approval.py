"""
Unit tests for the support approval endpoints introduced in T18.

The router gains three endpoints under
``/api/support/responses/{request_id}/`` that resume the LangGraph
support-review workflow at the approval gate:

- ``POST .../approve``  — ``Command(resume={"approved": True, ...})``
- ``POST .../reject``   — ``Command(resume={"approved": False, ...})``
- ``GET  .../approval-status`` — reads the thread's snapshot

The tests below mock ``workflows.support_review`` so the LangGraph
internals stay out of scope and cover the contract:

- The endpoints look up the graph on ``app.state.support_review_graph``
  and return 503 when no graph was wired (DB-less boot).
- Approve / reject return 404 when no thread exists for ``request_id``.
- Approve / reject return 409 when the thread is no longer pending
  (already approved/rejected/completed) so a duplicate decision cannot
  silently re-run the graph.
- The successful paths surface 200 with ``request_id``, ``status``, and
  the ``approver`` / ``approved_at`` (or ``rejected_at``) timestamp.
- approval-status returns the snapshot the helper produced.
"""

from __future__ import annotations

import uuid
from unittest import mock

import fastapi
import pytest
from fastapi.testclient import TestClient

from sentinel.domain.approval import entities as approval_entities
from sentinel.interfaces.api import middleware as middleware_mod
from sentinel.interfaces.api.routers.support import router as support_router_mod
from sentinel.interfaces.workflows import support_review as workflows_support_review


_REQUEST_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
_REQUEST_ID = str(_REQUEST_UUID)


def _build_app(*, graph: mock.MagicMock | None) -> fastapi.FastAPI:
    """Wire only the support router with ``graph`` on app.state."""
    app = fastapi.FastAPI()
    app.add_middleware(middleware_mod.RequestIdMiddleware)
    app.include_router(support_router_mod.router, prefix="/api")
    app.state.support_review_graph = graph
    return app


def _pending_status() -> workflows_support_review.ReviewStatus:
    return workflows_support_review.ReviewStatus(
        request_id=_REQUEST_UUID,
        status="pending",
        needs_approval=True,
        approval_decision=None,
    )


def _patch_status(monkeypatch: pytest.MonkeyPatch, status):
    async def fake_status(*, request_id, graph):
        return status

    monkeypatch.setattr(
        support_router_mod.workflows_support_review,
        "get_review_status",
        fake_status,
    )


def _patch_resume(monkeypatch: pytest.MonkeyPatch, captured: dict):
    async def fake_resume(*, request_id, decision, graph, approver=None, reason=None):
        captured["resume"] = {
            "request_id": request_id,
            "decision": decision,
            "graph": graph,
            "approver": approver,
            "reason": reason,
        }
        return workflows_support_review.ReviewOutcome(
            request_id=request_id,
            response_suggestion=None,
            confidence=None,
            needs_approval=False,
            interrupt_payload=None,
            approval_decision=decision,
        )

    monkeypatch.setattr(
        support_router_mod.workflows_support_review,
        "resume_review",
        fake_resume,
    )


@pytest.fixture
def captured() -> dict:
    return {}


class TestApproveEndpoint:
    def test_approves_pending_thread_with_200(
        self, monkeypatch: pytest.MonkeyPatch, captured: dict
    ) -> None:
        # Given a thread paused at the approval gate
        graph_stub = mock.MagicMock(name="SupportGraph")
        _patch_status(monkeypatch, _pending_status())
        _patch_resume(monkeypatch, captured)
        client = TestClient(_build_app(graph=graph_stub))

        # When an approver POSTs the approve endpoint
        response = client.post(
            f"/api/support/responses/{_REQUEST_ID}/approve",
            json={"approver": "alice@example.com", "edits": "Tighten phrasing"},
        )

        # Then the workflow resumes with APPROVED and the response
        # carries the approval audit fields
        assert response.status_code == 200
        body = response.json()
        assert body["request_id"] == _REQUEST_ID
        assert body["status"] == "approved"
        assert body["approver"] == "alice@example.com"
        assert "approved_at" in body

        # And resume_review received the right inputs
        recorded = captured["resume"]
        assert recorded["request_id"] == _REQUEST_UUID
        assert recorded["decision"] is approval_entities.ApprovalDecision.APPROVED
        assert recorded["graph"] is graph_stub
        assert recorded["approver"] == "alice@example.com"
        assert recorded["reason"] == "Tighten phrasing"

    def test_returns_404_when_thread_missing(
        self, monkeypatch: pytest.MonkeyPatch, captured: dict
    ) -> None:
        # Given the helper reports no checkpoint for the request_id
        _patch_status(monkeypatch, None)

        async def should_not_run(**kwargs):  # pragma: no cover - assertion guard
            captured["resume"] = kwargs

        monkeypatch.setattr(
            support_router_mod.workflows_support_review,
            "resume_review",
            should_not_run,
        )
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When the approve endpoint is called
        response = client.post(
            f"/api/support/responses/{_REQUEST_ID}/approve",
            json={"approver": "alice@example.com"},
        )

        # Then the request is rejected with 404 and resume was never called
        assert response.status_code == 404
        body = response.json()
        assert body["error"] == "support_review_thread_not_found"
        assert body["request_id"] == _REQUEST_ID
        assert "resume" not in captured

    def test_returns_409_when_thread_already_decided(
        self, monkeypatch: pytest.MonkeyPatch, captured: dict
    ) -> None:
        # Given the thread is already approved
        already_approved = workflows_support_review.ReviewStatus(
            request_id=_REQUEST_UUID,
            status="approved",
            needs_approval=True,
            approval_decision=approval_entities.ApprovalDecision.APPROVED,
        )
        _patch_status(monkeypatch, already_approved)

        async def should_not_run(**kwargs):  # pragma: no cover - assertion guard
            captured["resume"] = kwargs

        monkeypatch.setattr(
            support_router_mod.workflows_support_review,
            "resume_review",
            should_not_run,
        )
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When a second approve attempt arrives
        response = client.post(
            f"/api/support/responses/{_REQUEST_ID}/approve",
            json={"approver": "alice@example.com"},
        )

        # Then the request is rejected with 409 conflict and resume was
        # not called a second time
        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "support_review_already_decided"
        assert body["status"] == "approved"
        assert "resume" not in captured

    def test_returns_503_when_graph_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, captured: dict
    ) -> None:
        # Given the lifespan never wired a graph
        client = TestClient(_build_app(graph=None))

        # When the approve endpoint is called
        response = client.post(
            f"/api/support/responses/{_REQUEST_ID}/approve",
            json={"approver": "alice@example.com"},
        )

        # Then the failure is surfaced as 503 immediately
        assert response.status_code == 503
        body = response.json()
        assert body["error"] == "support_review_graph_unavailable"


class TestRejectEndpoint:
    def test_rejects_pending_thread_with_200(
        self, monkeypatch: pytest.MonkeyPatch, captured: dict
    ) -> None:
        # Given a thread paused at the approval gate
        graph_stub = mock.MagicMock(name="SupportGraph")
        _patch_status(monkeypatch, _pending_status())
        _patch_resume(monkeypatch, captured)
        client = TestClient(_build_app(graph=graph_stub))

        # When an approver POSTs the reject endpoint with a reason
        response = client.post(
            f"/api/support/responses/{_REQUEST_ID}/reject",
            json={"approver": "bob@example.com", "reason": "Cited the wrong runbook"},
        )

        # Then the workflow resumes with REJECTED and the response
        # carries the rejection audit fields
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "rejected"
        assert body["approver"] == "bob@example.com"
        assert "rejected_at" in body

        # And resume_review received the rejection plus the reason
        recorded = captured["resume"]
        assert recorded["decision"] is approval_entities.ApprovalDecision.REJECTED
        assert recorded["approver"] == "bob@example.com"
        assert recorded["reason"] == "Cited the wrong runbook"

    def test_returns_404_when_thread_missing(
        self, monkeypatch: pytest.MonkeyPatch, captured: dict
    ) -> None:
        # Given no checkpoint exists for the request_id
        _patch_status(monkeypatch, None)
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When the reject endpoint is called
        response = client.post(
            f"/api/support/responses/{_REQUEST_ID}/reject",
            json={"approver": "bob@example.com"},
        )

        # Then the request is rejected with 404
        assert response.status_code == 404


class TestApprovalStatusEndpoint:
    def test_returns_status_payload_for_pending_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a thread paused at the approval gate
        _patch_status(monkeypatch, _pending_status())
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When the status endpoint is called
        response = client.get(f"/api/support/responses/{_REQUEST_ID}/approval-status")

        # Then the snapshot is surfaced as 200 OK
        assert response.status_code == 200
        body = response.json()
        assert body["request_id"] == _REQUEST_ID
        assert body["status"] == "pending"
        assert body["needs_approval"] is True
        assert body["approval_decision"] is None

    def test_returns_decision_value_when_approved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given an already-approved thread
        approved = workflows_support_review.ReviewStatus(
            request_id=_REQUEST_UUID,
            status="approved",
            needs_approval=True,
            approval_decision=approval_entities.ApprovalDecision.APPROVED,
        )
        _patch_status(monkeypatch, approved)
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When the status endpoint is called
        response = client.get(f"/api/support/responses/{_REQUEST_ID}/approval-status")

        # Then the decision string surfaces in the body
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["approval_decision"] == "approved"

    def test_returns_404_when_thread_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given no checkpoint exists
        _patch_status(monkeypatch, None)
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When the status endpoint is called
        response = client.get(f"/api/support/responses/{_REQUEST_ID}/approval-status")

        # Then the request is rejected with 404
        assert response.status_code == 404

    def test_returns_503_when_graph_unavailable(self) -> None:
        # Given the lifespan never wired a graph
        client = TestClient(_build_app(graph=None))

        # When the status endpoint is called
        response = client.get(f"/api/support/responses/{_REQUEST_ID}/approval-status")

        # Then the failure is surfaced as 503
        assert response.status_code == 503
