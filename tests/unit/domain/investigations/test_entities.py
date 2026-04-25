from __future__ import annotations

from datetime import UTC, datetime

from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.investigations import entities as investigation_entities


class TestInvestigation:
    def test_create_investigation_defaults(self):
        alert = alert_entities.Alert(
            id="123",
            source="pagerduty",
            title="Test",
            description="Test",
            severity=alert_entities.AlertSeverity.MEDIUM,
            service="test",
            triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        investigation = investigation_entities.Investigation(alert=alert)
        assert investigation.status == investigation_entities.InvestigationStatus.PENDING
        assert investigation.findings == []
        assert investigation.root_cause is None
        assert investigation.remediation is None

    def test_investigation_with_findings(self):
        alert = alert_entities.Alert(
            id="123",
            source="pagerduty",
            title="Test",
            description="Test",
            severity=alert_entities.AlertSeverity.HIGH,
            service="test",
            triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        finding = investigation_entities.Finding(
            source="datadog_logs",
            summary="Error rate increased 5x in last 10 minutes",
            relevance=0.9,
        )
        investigation = investigation_entities.Investigation(
            alert=alert,
            status=investigation_entities.InvestigationStatus.COMPLETED,
            findings=[finding],
            root_cause="Database connection pool exhausted",
            remediation="1. Increase pool size\n2. Add connection timeout",
            confidence_score=0.85,
        )
        assert len(investigation.findings) == 1
        assert investigation.findings[0].source == "datadog_logs"
        assert investigation.root_cause == "Database connection pool exhausted"
