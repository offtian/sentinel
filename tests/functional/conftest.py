from __future__ import annotations

from dataclasses import dataclass

import pytest

from sentinel.domain.search import searcher
from sentinel.domain.sre import holmes_adapter
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    response_drafter,
    root_cause_analyser,
    ticket_reviewer,
)
from tests import factories


@dataclass(frozen=True)
class FakeAgentResult[T]:
    output: T


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


@pytest.fixture
def patch_alert_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic alert classification — always returns infrastructure/high."""

    async def fake_run(*, user_prompt, model, deps, **kwargs):
        return FakeAgentResult(
            alert_classifier.AlertClassification(
                severity="high",
                affected_service="api-service",
                category="infrastructure",
                summary="Pod OOMKilled causing 5xx errors",
                requires_immediate_action=True,
            )
        )

    monkeypatch.setattr(alert_classifier.agent, "run", fake_run)


@pytest.fixture
def patch_root_cause_analyser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic root cause analysis with high confidence."""

    async def fake_run(*, user_prompt, model, deps, **kwargs):
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

    monkeypatch.setattr(root_cause_analyser.agent, "run", fake_run)


@pytest.fixture
def patch_ticket_reviewer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic ticket classification."""

    async def fake_run(*, user_prompt, model, deps, **kwargs):
        return FakeAgentResult(
            ticket_reviewer.TicketClassification(
                category="account",
                urgency="high",
                required_expertise=["authentication", "SSO"],
                key_questions=["Is the user's SSO session expired?"],
                search_queries=["SSO login troubleshooting", "password reset guide"],
            )
        )

    monkeypatch.setattr(ticket_reviewer.agent, "run", fake_run)


@pytest.fixture
def patch_response_drafter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic response drafting."""

    async def fake_run(*, user_prompt, model, deps, **kwargs):
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

    monkeypatch.setattr(response_drafter.agent, "run", fake_run)


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
