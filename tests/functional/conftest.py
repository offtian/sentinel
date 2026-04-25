from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest import mock

import pytest

from sentinel.domain.investigations import holmes_adapter
from sentinel.domain.search import searcher
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    response_drafter,
    root_cause_analyser,
    ticket_reviewer,
)
from tests import factories


@dataclass(frozen=True)
class _FakeUsage:
    """Stub usage data for FakeAgentResult."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class FakeAgentResult[T]:
    output: T

    def usage(self) -> _FakeUsage:
        """Return a zero-token usage object for testing."""
        return _FakeUsage()

    def all_messages(self) -> list:
        """Return an empty message list for testing."""
        return []


def _make_fake_agent(fake_run: Any) -> mock.MagicMock:
    """Build a mock agent whose ``.run`` is the given async callable."""
    agent = mock.MagicMock()
    agent.run = fake_run
    return agent


def _build_fake_config(agent_overrides: dict[str, Any]) -> mock.MagicMock:
    """
    Build a mock config whose ``agent_for()`` returns fake agents.

    Any agent name not in ``agent_overrides`` returns a default MagicMock.
    """
    cfg = mock.MagicMock()
    cfg.agent_for = mock.MagicMock(
        side_effect=lambda name: agent_overrides.get(name, mock.MagicMock())
    )
    return cfg


@pytest.fixture
def mock_holmes() -> factories.MockHolmesAdapter:
    return factories.MockHolmesAdapter(
        result=holmes_adapter.HolmesInvestigationResult(
            analysis=(
                "Datadog logs show a 5x spike in 5xx errors starting at 14:32 UTC. "
                "Kubernetes pod api-service-7b8c was OOMKilled at 14:30 UTC. "
                "Memory usage ramped from 512Mi to 2Gi over 10 minutes."
            ),
            tool_calls=[
                {
                    "tool": "datadog_query_logs",
                    "result": "5xx errors: 250/min (baseline 50/min)",
                },
                {
                    "tool": "kubernetes_get_pods",
                    "result": "api-service-7b8c OOMKilled 14:30 UTC",
                },
            ],
            sources_queried=["datadog_logs", "kubernetes"],
        )
    )


async def _fake_alert_classifier_run(*, user_prompt, deps, **kwargs):
    """Deterministic alert classification — always returns infrastructure/high."""
    return FakeAgentResult(
        alert_classifier.AlertClassification(
            severity="high",
            affected_service="api-service",
            category="infrastructure",
            summary="Pod OOMKilled causing 5xx errors",
            requires_immediate_action=True,
        )
    )


async def _fake_root_cause_analyser_run(*, user_prompt, deps, **kwargs):
    """Deterministic root cause analysis with high confidence."""
    return FakeAgentResult(
        root_cause_analyser.RootCauseAnalysis(
            root_cause="Memory leak in request handler caused OOMKill",
            confidence=0.85,
            evidence=[
                "5xx errors spiked 5x at 14:32 UTC",
                "Pod OOMKilled at 14:30 UTC with 2Gi usage",
            ],
            remediation_steps=[
                "Increase memory limit to 4Gi",
                "Deploy fix for memory leak in handler",
            ],
            affected_services=["api-service"],
            timeline="14:20 memory ramp → 14:30 OOMKill → 14:32 5xx spike",
        )
    )


async def _fake_ticket_reviewer_run(*, user_prompt, deps, **kwargs):
    """Deterministic ticket classification."""
    return FakeAgentResult(
        ticket_reviewer.TicketClassification(
            category="account",
            urgency="high",
            required_expertise=["authentication", "SSO"],
            key_questions=["Is the user's SSO session expired?"],
            search_queries=["SSO login troubleshooting", "password reset guide"],
        )
    )


async def _fake_response_drafter_run(*, user_prompt, deps, **kwargs):
    """Deterministic response drafting."""
    return FakeAgentResult(
        response_drafter.DraftedResponse(
            response=(
                "Hi Jane,\n\n"
                "It sounds like your SSO session may have expired. Please try:\n"
                "1. Clear your browser cookies\n"
                "2. Visit /account/reset to reset your password\n"
                "3. Contact your IT admin if SSO is managed centrally\n\n"
                "Let us know if this resolves the issue."
            ),
            sources_used=[
                response_drafter.SourceReference(
                    title="Login Troubleshooting Guide",
                    url="https://docs.example.com/login",
                ),
            ],
            confidence=0.82,
            notes_for_agent="User may need IT admin involvement if SSO-managed.",
        )
    )


@pytest.fixture
def fake_sre_config() -> mock.MagicMock:
    """Configuration mock with fake alert_classifier and root_cause_analyser agents."""
    return _build_fake_config(
        {
            "alert_classifier": _make_fake_agent(_fake_alert_classifier_run),
            "root_cause_analyser": _make_fake_agent(_fake_root_cause_analyser_run),
        }
    )


@pytest.fixture
def fake_support_config() -> mock.MagicMock:
    """Configuration mock with fake ticket_reviewer and response_drafter agents."""
    return _build_fake_config(
        {
            "ticket_reviewer": _make_fake_agent(_fake_ticket_reviewer_run),
            "response_drafter": _make_fake_agent(_fake_response_drafter_run),
        }
    )


class StubDocumentSearcher(searcher.BaseDocumentSearcher):
    def __init__(self, results: list[searcher.DocumentSearchResult] | None = None) -> None:
        self._results = results or [
            searcher.DocumentSearchResult(
                id="doc-1",
                title="Login Troubleshooting Guide",
                excerpt="To reset your password, visit /account/reset.",
                url="https://docs.example.com/login",
                relevance=0.92,
            ),
            searcher.DocumentSearchResult(
                id="doc-2",
                title="SSO Configuration",
                excerpt="SSO sessions expire after 24 hours by default.",
                url="https://docs.example.com/sso",
                relevance=0.78,
            ),
        ]

    async def search(self, *, query: str, limit: int) -> list[searcher.DocumentSearchResult]:
        return self._results[:limit]


class StubPastTicketSearcher(searcher.BasePastTicketSearcher):
    def __init__(self, results: list[searcher.TicketSearchResult] | None = None) -> None:
        self._results = results or [
            searcher.TicketSearchResult(
                id="5001",
                key="SUPPORT-12",
                summary="User unable to log in after password change",
                description="After changing password via SSO portal, user locked out.",
                resolution="Cleared SSO cache and re-synced credentials.",
                url="https://jira.example.com/browse/SUPPORT-12",
                relevance=0.85,
            ),
        ]

    async def search(self, *, query: str, limit: int) -> list[searcher.TicketSearchResult]:
        return self._results[:limit]


class EmptyDocumentSearcher(searcher.BaseDocumentSearcher):
    async def search(self, *, query: str, limit: int) -> list[searcher.DocumentSearchResult]:
        return []


class EmptyPastTicketSearcher(searcher.BasePastTicketSearcher):
    async def search(self, *, query: str, limit: int) -> list[searcher.TicketSearchResult]:
        return []
