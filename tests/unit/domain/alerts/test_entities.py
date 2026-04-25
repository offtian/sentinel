from __future__ import annotations

from datetime import UTC, datetime

from sentinel.domain.alerts import entities as alert_entities


class TestAlert:
    def test_create_alert(self):
        alert = alert_entities.Alert(
            id="P123ABC",
            source="pagerduty",
            title="High CPU usage on web-01",
            description="CPU usage exceeded 90%",
            severity=alert_entities.AlertSeverity.HIGH,
            service="api-service",
            triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        assert alert.id == "P123ABC"
        assert alert.source == "pagerduty"
        assert alert.severity == alert_entities.AlertSeverity.HIGH
        assert alert.raw_payload == {}

    def test_alert_with_raw_payload(self):
        payload = {"event": {"data": {"id": "123"}}}
        alert = alert_entities.Alert(
            id="123",
            source="datadog",
            title="Test",
            description="Test alert",
            severity=alert_entities.AlertSeverity.LOW,
            service="test",
            triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
            raw_payload=payload,
        )
        assert alert.raw_payload == payload


class TestAlertSeverity:
    def test_severity_values(self):
        assert alert_entities.AlertSeverity.CRITICAL.value == "critical"
        assert alert_entities.AlertSeverity.HIGH.value == "high"
        assert alert_entities.AlertSeverity.MEDIUM.value == "medium"
        assert alert_entities.AlertSeverity.LOW.value == "low"
