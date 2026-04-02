from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import holmes_adapter
from sentinel.domain.support import entities as support_entities


def make_alert(
    *,
    alert_id: str = "P123ABC",
    source: str = "pagerduty",
    title: str = "High CPU usage on web-01",
    description: str = "CPU usage exceeded 90% for 5 minutes",
    severity: sre_entities.AlertSeverity = sre_entities.AlertSeverity.HIGH,
    service: str = "api-service",
    triggered_at: datetime | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> sre_entities.Alert:
    return sre_entities.Alert(
        id=alert_id,
        source=source,  # type: ignore[arg-type]
        title=title,
        description=description,
        severity=severity,
        service=service,
        triggered_at=triggered_at or datetime(2024, 1, 1, tzinfo=UTC),
        raw_payload=raw_payload or {},
    )


def make_finding(
    *,
    source: str = "datadog_logs",
    summary: str = "Error rate increased 5x in last 10 minutes",
    relevance: float = 0.9,
    raw_data: str | None = None,
) -> sre_entities.Finding:
    return sre_entities.Finding(
        source=source,
        summary=summary,
        relevance=relevance,
        raw_data=raw_data,
    )


def make_investigation(
    *,
    alert: sre_entities.Alert | None = None,
    status: sre_entities.InvestigationStatus = sre_entities.InvestigationStatus.PENDING,
    findings: list[sre_entities.Finding] | None = None,
    root_cause: str | None = None,
    remediation: str | None = None,
    confidence_score: float | None = None,
) -> sre_entities.Investigation:
    return sre_entities.Investigation(
        alert=alert or make_alert(),
        status=status,
        findings=findings or [],
        root_cause=root_cause,
        remediation=remediation,
        confidence_score=confidence_score,
    )


def make_ticket(
    *,
    ticket_id: str = "10001",
    key: str = "SUPPORT-42",
    summary: str = "Cannot log in to dashboard",
    description: str = "I've been unable to log in since yesterday morning.",
    reporter: str = "Jane Doe",
    priority: str = "High",
    created_at: datetime | None = None,
    labels: list[str] | None = None,
    comments: list[support_entities.TicketComment] | None = None,
) -> support_entities.Ticket:
    return support_entities.Ticket(
        id=ticket_id,
        key=key,
        summary=summary,
        description=description,
        reporter=reporter,
        priority=priority,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
        labels=labels or [],
        comments=comments or [],
    )


def make_doc_source(
    *,
    title: str = "Login Troubleshooting Guide",
    url: str = "https://docs.example.com/login",
    source_type: str = "confluence",
    excerpt: str = "To reset your password, visit the account portal...",
    relevance: float = 0.9,
) -> support_entities.DocSource:
    return support_entities.DocSource(
        title=title,
        url=url,
        source_type=source_type,  # type: ignore[arg-type]
        excerpt=excerpt,
        relevance=relevance,
    )


def make_response_suggestion(
    *,
    ticket_id: str = "10001",
    suggested_response: str = "Based on our docs, you can reset your password at /account/reset.",
    sources: list[support_entities.DocSource] | None = None,
    confidence_score: float | None = 0.85,
    category: str | None = "account",
) -> support_entities.ResponseSuggestion:
    return support_entities.ResponseSuggestion(
        ticket_id=ticket_id,
        suggested_response=suggested_response,
        sources=sources or [make_doc_source()],
        confidence_score=confidence_score,
        category=category,
    )


def make_confidence_score(
    *,
    total: float = 0.8,
) -> confidence_entities.ConfidenceScore:
    return confidence_entities.ConfidenceScore.from_total(total)


class MockHolmesAdapter(holmes_adapter.BaseHolmesAdapter):
    """Mock adapter for testing."""

    def __init__(self, *, result: holmes_adapter.HolmesInvestigationResult | None = None) -> None:
        self._result = result or holmes_adapter.HolmesInvestigationResult(
            analysis="Mock investigation: no issues found.",
            tool_calls=[
                {"tool": "datadog_query_logs", "result": "No errors in last 30 minutes"},
                {"tool": "kubernetes_get_pods", "result": "All pods healthy"},
            ],
            sources_queried=["datadog_logs", "kubernetes"],
        )

    @property
    def is_configured(self) -> bool:
        return True

    async def investigate(
        self,
        *,
        alert: sre_entities.Alert,
        context: holmes_adapter.investigation.InvestigationContext | None = None,
    ) -> holmes_adapter.HolmesInvestigationResult:
        return self._result
