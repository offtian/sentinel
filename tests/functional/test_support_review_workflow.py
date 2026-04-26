"""
Functional E2E tests for the LangGraph support-review workflow at the
webhook boundary (T19, T20).

These tests boot the production FastAPI app via ``TestClient`` so the
lifespan (T15) wires the ``AsyncPostgresSaver`` checkpointer + compiled
support graph against the real test Postgres. PydanticAI agents are
swapped out via the ``fake_support_config`` fixture from
``tests/functional/conftest.py``; the document / past-ticket searchers
are stubbed so the run is deterministic.

The two tests cover the design-spec branches:

- **T19 high-confidence path** — webhook fires, the graph runs to
  ``END`` without pausing, the response signals ``needs_approval=False``
  and a populated ``suggestion_id``.
- **T20 low-confidence interrupt** — webhook fires, the graph pauses at
  ``wait_for_human``, the response surfaces the interrupt payload; a
  follow-up POST to ``/responses/{request_id}/approve`` resumes the
  workflow through to ``END`` and records the approval.

Both tests monkeypatch ``support_ops.persist_ticket_review`` so the
audit row is captured in-memory rather than written to the support
review table -- the row's data layer is exercised in
``tests/integration/`` and is out of scope for these workflow E2E
tests.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from sentinel import config as config_mod
from sentinel.domain.support import operations as support_ops
from sentinel.interfaces.api import app as api_app
from sentinel.interfaces.workflows import support_review as workflows_support_review
from tests.functional.conftest import StubDocumentSearcher, StubPastTicketSearcher


def _jira_payload(*, key: str = "SUPPORT-9", issue_id: str = "200") -> dict[str, Any]:
    return {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "id": issue_id,
            "key": key,
            "fields": {
                "summary": "Cannot log in to dashboard",
                "description": "Locked out and password reset is not working.",
                "project": {"key": "SUPPORT"},
                "reporter": {"displayName": "Jane"},
                "priority": {"name": "High"},
                "labels": [],
            },
        },
    }


@pytest.fixture
def configured_support_config(fake_support_config: mock.MagicMock) -> mock.MagicMock:
    """Augment the fake support config with concrete searchers + toolset stubs.

    The ``fake_support_config`` fixture from conftest already wires the
    PydanticAI agents to canned outputs; here we add the surrounding
    search + toolset surface that the LangGraph nodes call ``get_config``
    for. Every searcher returns the deterministic stub fixtures so the
    drafter's confidence reduces to a known 0.61 (computed by
    ``ConfidenceScore.from_factors`` over one source / 5 max + relevance
    0.82 + recency 0.7) regardless of node ordering.
    """
    cfg = fake_support_config
    cfg.build_document_searcher = lambda: StubDocumentSearcher()
    cfg.build_ticket_searcher = lambda: StubPastTicketSearcher()
    cfg.build_ticket_triage_toolset = lambda: None
    cfg.build_support_search_toolset = lambda: None
    cfg.envelope_strict_mode = False
    return cfg


@pytest.fixture
def captured_persistence(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """No-op the audit row persistence and capture the calls."""
    captured: dict[str, Any] = {"calls": []}

    async def fake_persist(**kwargs: Any) -> uuid.UUID:
        captured["calls"].append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(support_ops, "persist_ticket_review", fake_persist)
    return captured


def _patch_config_singleton(monkeypatch: pytest.MonkeyPatch, cfg: mock.MagicMock) -> None:
    """Patch ``get_config`` in both the lifespan source and workflow module.

    The lifespan reads ``config_mod.get_config`` at startup; the
    workflow nodes read ``workflows_support_review.get_config`` at node
    invocation. Both must point at the fake config so the run uses
    canned PydanticAI agents and stub searchers.
    """
    monkeypatch.setattr(config_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(workflows_support_review, "get_config", lambda: cfg)


class TestSupportReviewWorkflowHighConfidence:
    """T19 — webhook fires, the graph runs to ``END`` without pausing."""

    def test_webhook_returns_completed_outcome(
        self,
        configured_support_config: mock.MagicMock,
        captured_persistence: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given a configuration whose approval threshold is loose enough
        # that the canned 0.61 drafter confidence still passes the gate
        configured_support_config.require_approval_below_confidence = 0.5
        _patch_config_singleton(monkeypatch, configured_support_config)
        request_id = str(uuid.uuid4())

        # When the production app boots through the lifespan and the
        # webhook fires for a fresh issue
        with TestClient(api_app.app) as client:
            response = client.post(
                "/api/support/webhooks/jira",
                json=_jira_payload(),
                headers={"X-Request-Id": request_id},
            )

        # Then the response signals a completed run with the design-spec
        # shape carrying the suggestion id and a null interrupt payload
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["request_id"] == request_id
        assert body["ticket_key"] == "SUPPORT-9"
        assert body["needs_approval"] is False
        assert body["interrupt_payload"] is None
        assert body["suggestion_id"] is not None

        # And the audit row was persisted exactly once with the
        # confidence the drafter actually computed
        assert len(captured_persistence["calls"]) == 1
        persisted = captured_persistence["calls"][0]
        assert persisted["ticket_key"] == "SUPPORT-9"
        assert persisted["confidence_score"] == pytest.approx(0.61, abs=0.01)


class TestSupportReviewWorkflowLowConfidenceInterrupt:
    """T20 — webhook pauses; approve endpoint resumes the run to ``END``."""

    def test_webhook_pauses_then_approve_endpoint_resumes(
        self,
        configured_support_config: mock.MagicMock,
        captured_persistence: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given a configuration whose approval threshold rejects the
        # canned 0.61 drafter confidence so the workflow pauses
        configured_support_config.require_approval_below_confidence = 0.7
        _patch_config_singleton(monkeypatch, configured_support_config)
        request_id = str(uuid.uuid4())

        # When the production app boots and the webhook fires the
        # workflow pauses at the approval gate, surfacing the interrupt
        # payload back to the caller
        with TestClient(api_app.app) as client:
            initial_response = client.post(
                "/api/support/webhooks/jira",
                json=_jira_payload(),
                headers={"X-Request-Id": request_id},
            )

            assert initial_response.status_code == 200, initial_response.text
            initial_body = initial_response.json()
            assert initial_body["request_id"] == request_id
            assert initial_body["needs_approval"] is True
            assert initial_body["interrupt_payload"] is not None
            assert initial_body["interrupt_payload"]["request_id"] == request_id
            assert initial_body["interrupt_payload"]["action"] == "approve_response_suggestion"

            # When the approve endpoint POSTs against the same request_id
            approve_response = client.post(
                f"/api/support/responses/{request_id}/approve",
                json={"approver": "alice@example.com", "edits": "Tighten phrasing"},
            )

        # Then the workflow resumes through to END and the response
        # records the approval audit fields
        assert approve_response.status_code == 200, approve_response.text
        approve_body = approve_response.json()
        assert approve_body["request_id"] == request_id
        assert approve_body["status"] == "approved"
        assert approve_body["approver"] == "alice@example.com"
        assert "approved_at" in approve_body

        # And the audit row was persisted on the initial run (resume
        # does not re-persist; the persistence call lives at the
        # webhook synchronisation point)
        assert len(captured_persistence["calls"]) == 1
