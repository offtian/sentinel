from __future__ import annotations

from datetime import UTC, datetime

from sentinel.domain.sre import entities


class TestAlert:
    def test_create_alert(self):
        alert = entities.Alert(
            id="P123ABC",
            source="pagerduty",
            title="High CPU usage on web-01",
            description="CPU usage exceeded 90%",
            severity=entities.AlertSeverity.HIGH,
            service="api-service",
            triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        assert alert.id == "P123ABC"
        assert alert.source == "pagerduty"
        assert alert.severity == entities.AlertSeverity.HIGH
        assert alert.raw_payload == {}

    def test_alert_with_raw_payload(self):
        payload = {"event": {"data": {"id": "123"}}}
        alert = entities.Alert(
            id="123",
            source="datadog",
            title="Test",
            description="Test alert",
            severity=entities.AlertSeverity.LOW,
            service="test",
            triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
            raw_payload=payload,
        )
        assert alert.raw_payload == payload


class TestInvestigation:
    def test_create_investigation_defaults(self):
        alert = entities.Alert(
            id="123",
            source="pagerduty",
            title="Test",
            description="Test",
            severity=entities.AlertSeverity.MEDIUM,
            service="test",
            triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        investigation = entities.Investigation(alert=alert)
        assert investigation.status == entities.InvestigationStatus.PENDING
        assert investigation.findings == []
        assert investigation.root_cause is None
        assert investigation.remediation is None

    def test_investigation_with_findings(self):
        alert = entities.Alert(
            id="123",
            source="pagerduty",
            title="Test",
            description="Test",
            severity=entities.AlertSeverity.HIGH,
            service="test",
            triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        finding = entities.Finding(
            source="datadog_logs",
            summary="Error rate increased 5x in last 10 minutes",
            relevance=0.9,
        )
        investigation = entities.Investigation(
            alert=alert,
            status=entities.InvestigationStatus.COMPLETED,
            findings=[finding],
            root_cause="Database connection pool exhausted",
            remediation="1. Increase pool size\n2. Add connection timeout",
            confidence_score=0.85,
        )
        assert len(investigation.findings) == 1
        assert investigation.findings[0].source == "datadog_logs"
        assert investigation.root_cause == "Database connection pool exhausted"


class TestAlertSeverity:
    def test_severity_values(self):
        assert entities.AlertSeverity.CRITICAL.value == "critical"
        assert entities.AlertSeverity.HIGH.value == "high"
        assert entities.AlertSeverity.MEDIUM.value == "medium"
        assert entities.AlertSeverity.LOW.value == "low"
