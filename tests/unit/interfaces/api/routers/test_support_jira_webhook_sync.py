"""
Unit tests for the synchronous Jira webhook flow on the support router (T17).

The webhook hard-cut in T17 swaps the legacy ``_enqueue_ticket`` queue
write for a direct ``workflows.support_review.review_ticket`` call
against the compiled graph stashed on ``app.state.support_review_graph``
during the lifespan (T15). The tests below mock that entrypoint so the
LangGraph internals stay out of scope and assert:

- The webhook surfaces the LangGraph outcome on a 200 OK with the
  design-spec shape ``{request_id, ticket_key, suggestion_id,
  needs_approval, interrupt_payload}``.
- The interrupt branch carries the ``__interrupt__`` payload back to
  the caller verbatim.
- The audit-row persistence helper is invoked with the suggestion the
  graph drafted, regardless of which branch fired.
- ``support_auto_draft=False`` still short-circuits before the graph is
  touched (legacy behaviour).
- A missing graph (DB-less boot) surfaces a 503 instead of a 500 so an
  operator sees the misconfiguration immediately.
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


def _settings_stub(*, auto_draft: bool = True) -> mock.MagicMock:
    settings_stub = mock.MagicMock()
    settings_stub.support_auto_draft = auto_draft
    settings_stub.k8s_cluster_name = "prod-eu-west-1"
    settings_stub.region = "eu-west-1"
    return settings_stub


def _config_stub() -> mock.MagicMock:
    config_stub = mock.MagicMock()
    config_stub.envelope_strict_mode = False
    return config_stub


def _build_app(*, graph: mock.MagicMock | None) -> fastapi.FastAPI:
    """Wire only the support router and stash ``graph`` on ``app.state``."""
    app = fastapi.FastAPI()
    app.add_middleware(middleware_mod.RequestIdMiddleware)
    app.include_router(support_router_mod.router, prefix="/api")
    app.state.support_review_graph = graph
    return app


def _jira_payload(*, key: str = "SUPPORT-9", summary: str = "Cannot log in") -> dict[str, Any]:
    return {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "id": "200",
            "key": key,
            "fields": {
                "summary": summary,
                "description": "I'm locked out and cannot reset my password.",
                "project": {"key": "SUPPORT"},
                "reporter": {"displayName": "Jane"},
                "priority": {"name": "High"},
                "labels": [],
            },
        },
    }


@pytest.fixture
def captured_call() -> dict[str, Any]:
    return {}


@pytest.fixture
def patched_settings(monkeypatch: pytest.MonkeyPatch) -> mock.MagicMock:
    settings = _settings_stub()
    monkeypatch.setattr(support_router_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(support_router_mod, "get_config", _config_stub)
    return settings


@pytest.fixture
def patched_persist(monkeypatch: pytest.MonkeyPatch, captured_call: dict[str, Any]):
    """Capture the audit-row persistence call without touching the DB."""

    async def fake_persist(**kwargs):
        captured_call["persist"] = kwargs
        return uuid.uuid4()

    monkeypatch.setattr(
        support_router_mod.support_ops,
        "persist_ticket_review",
        fake_persist,
    )

    db_stub = mock.MagicMock()
    monkeypatch.setattr(
        support_router_mod.async_db,
        "get_db",
        lambda: db_stub,
    )


def _patch_review_ticket(
    monkeypatch: pytest.MonkeyPatch,
    captured_call: dict[str, Any],
    *,
    outcome: workflows_support_review.ReviewOutcome,
):
    """Patch the synchronous entrypoint and capture its kwargs."""

    async def fake_review_ticket(**kwargs):
        captured_call["review_ticket"] = kwargs
        return outcome

    monkeypatch.setattr(
        support_router_mod.workflows_support_review,
        "review_ticket",
        fake_review_ticket,
    )


class TestJiraWebhookHighConfidencePath:
    def test_returns_completed_outcome_on_200(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_settings: mock.MagicMock,
        patched_persist: None,
        captured_call: dict[str, Any],
    ) -> None:
        # Given a graph that completes through to END with a populated
        # response suggestion (high-confidence happy path)
        suggestion = factories.make_response_suggestion(ticket_id="200")
        confidence = factories.make_confidence_score(total=0.85)
        outcome = workflows_support_review.ReviewOutcome(
            request_id=_REQUEST_UUID,
            response_suggestion=suggestion,
            confidence=confidence,
            needs_approval=False,
            interrupt_payload=None,
            approval_decision=None,
        )
        graph_stub = mock.MagicMock(name="SupportReviewGraph")
        _patch_review_ticket(monkeypatch, captured_call, outcome=outcome)
        client = TestClient(_build_app(graph=graph_stub))

        # When the Jira webhook fires for a fresh issue
        response = client.post(
            "/api/support/webhooks/jira",
            json=_jira_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the response surfaces the design-spec shape with the
        # suggestion id and a null interrupt payload
        assert response.status_code == 200
        body = response.json()
        assert body["request_id"] == _VALID_REQUEST_ID
        assert body["ticket_key"] == "SUPPORT-9"
        assert body["needs_approval"] is False
        assert body["interrupt_payload"] is None
        assert body["suggestion_id"] == str(suggestion.id)

        # And the entrypoint received the same graph that was stashed on
        # app.state plus an envelope minted from the X-Request-Id header
        review_kwargs = captured_call["review_ticket"]
        assert review_kwargs["graph"] is graph_stub
        assert str(review_kwargs["envelope"].request_id) == _VALID_REQUEST_ID
        assert review_kwargs["ticket"].key == "SUPPORT-9"

        # And the audit row is persisted with the drafted suggestion
        persisted = captured_call["persist"]
        assert persisted["ticket_id"] == "200"
        assert persisted["ticket_key"] == "SUPPORT-9"
        assert persisted["suggested_response"] == suggestion.suggested_response
        assert persisted["confidence_score"] == confidence.total


class TestJiraWebhookLowConfidencePath:
    def test_returns_interrupt_payload_on_pause(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patched_settings: mock.MagicMock,
        patched_persist: None,
        captured_call: dict[str, Any],
    ) -> None:
        # Given a graph that paused at the approval gate carrying an
        # interrupt payload describing the suggestion awaiting review
        suggestion = factories.make_response_suggestion(ticket_id="200")
        confidence = factories.make_confidence_score(total=0.45)
        interrupt_value = {
            "action": "approve_response_suggestion",
            "request_id": _VALID_REQUEST_ID,
            "suggestion_id": str(suggestion.id),
            "confidence_total": 0.45,
            "confidence_label": "Low",
        }
        outcome = workflows_support_review.ReviewOutcome(
            request_id=_REQUEST_UUID,
            response_suggestion=suggestion,
            confidence=confidence,
            needs_approval=True,
            interrupt_payload=interrupt_value,
            approval_decision=None,
        )
        _patch_review_ticket(monkeypatch, captured_call, outcome=outcome)
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When the webhook fires
        response = client.post(
            "/api/support/webhooks/jira",
            json=_jira_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the response signals approval is required and surfaces
        # the interrupt payload verbatim
        assert response.status_code == 200
        body = response.json()
        assert body["needs_approval"] is True
        assert body["interrupt_payload"] == interrupt_value
        assert body["suggestion_id"] == str(suggestion.id)

        # And the audit row is still persisted (every run leaves a trail)
        assert captured_call["persist"]["confidence_score"] == confidence.total


class TestJiraWebhookAutoDraftDisabled:
    def test_skips_graph_when_auto_draft_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        captured_call: dict[str, Any],
    ) -> None:
        # Given the support_auto_draft toggle is off
        monkeypatch.setattr(
            support_router_mod, "get_settings", lambda: _settings_stub(auto_draft=False)
        )
        monkeypatch.setattr(support_router_mod, "get_config", _config_stub)
        review_calls: list[Any] = []

        async def should_not_run(**kwargs):
            review_calls.append(kwargs)

        monkeypatch.setattr(
            support_router_mod.workflows_support_review,
            "review_ticket",
            should_not_run,
        )
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When the webhook fires
        response = client.post(
            "/api/support/webhooks/jira",
            json=_jira_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the response advertises auto_draft=False and the graph
        # entrypoint was never called
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "received"
        assert body["auto_draft"] is False
        assert review_calls == []


class TestJiraWebhookGraphMissing:
    def test_returns_503_when_graph_unavailable(
        self,
        patched_settings: mock.MagicMock,
        captured_call: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the lifespan did not build a graph (DB-less boot)
        async def should_not_run(**kwargs):
            captured_call["review_ticket"] = kwargs

        monkeypatch.setattr(
            support_router_mod.workflows_support_review,
            "review_ticket",
            should_not_run,
        )
        client = TestClient(_build_app(graph=None))

        # When the webhook fires
        response = client.post(
            "/api/support/webhooks/jira",
            json=_jira_payload(),
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the request is failed-fast with 503 and the entrypoint
        # was never called
        assert response.status_code == 503
        body = json.loads(response.content)
        assert body["error"] == "support_review_graph_unavailable"
        assert "review_ticket" not in captured_call


class TestJiraWebhookSkippedEvents:
    def test_returns_200_skipped_for_unknown_event_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
        captured_call: dict[str, Any],
    ) -> None:
        # Given an event type the handler does not act on
        monkeypatch.setattr(support_router_mod, "get_settings", lambda: _settings_stub())
        monkeypatch.setattr(support_router_mod, "get_config", _config_stub)
        review_calls: list[Any] = []

        async def should_not_run(**kwargs):
            review_calls.append(kwargs)

        monkeypatch.setattr(
            support_router_mod.workflows_support_review,
            "review_ticket",
            should_not_run,
        )
        client = TestClient(_build_app(graph=mock.MagicMock()))

        # When a non-actionable event fires
        response = client.post(
            "/api/support/webhooks/jira",
            json={"webhookEvent": "jira:issue_deleted", "issue": {}},
            headers={"X-Request-Id": _VALID_REQUEST_ID},
        )

        # Then the handler returns the legacy "skipped" envelope and the
        # graph is never touched
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "skipped"
        assert review_calls == []
