from __future__ import annotations

from sentinel.domain.sre import entities
from sentinel.interfaces.webhooks import pagerduty


class TestParsePagerDutyWebhook:
    def test_parse_triggered_incident(self):
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P123ABC",
                    "title": "High CPU usage on web-server-01",
                    "urgency": "high",
                    "service": {"summary": "Production API"},
                    "body": {"details": "CPU usage exceeded 90%"},
                    "created_at": "2024-01-01T00:00:00Z",
                },
            }
        }
        alert = pagerduty.parse_pagerduty_webhook(payload)

        assert alert is not None
        assert alert.id == "P123ABC"
        assert alert.source == "pagerduty"
        assert alert.title == "High CPU usage on web-server-01"
        assert alert.severity == entities.AlertSeverity.HIGH
        assert alert.service == "Production API"
        assert alert.description == "CPU usage exceeded 90%"

    def test_parse_escalated_incident(self):
        payload = {
            "event": {
                "event_type": "incident.escalated",
                "data": {
                    "id": "P456DEF",
                    "title": "Database connection failure",
                    "urgency": "high",
                    "service": {"summary": "Database"},
                    "body": {"details": "Connection pool exhausted"},
                },
            }
        }
        alert = pagerduty.parse_pagerduty_webhook(payload)
        assert alert is not None
        assert alert.id == "P456DEF"

    def test_skip_acknowledged_event(self):
        payload = {
            "event": {
                "event_type": "incident.acknowledged",
                "data": {"id": "P789GHI"},
            }
        }
        alert = pagerduty.parse_pagerduty_webhook(payload)
        assert alert is None

    def test_skip_resolved_event(self):
        payload = {
            "event": {
                "event_type": "incident.resolved",
                "data": {"id": "P789GHI"},
            }
        }
        alert = pagerduty.parse_pagerduty_webhook(payload)
        assert alert is None

    def test_low_urgency_maps_to_low_severity(self):
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "P100",
                    "title": "Minor issue",
                    "urgency": "low",
                    "service": {"summary": "Batch Jobs"},
                    "body": {"details": "Non-critical batch job delayed"},
                },
            }
        }
        alert = pagerduty.parse_pagerduty_webhook(payload)
        assert alert is not None
        assert alert.severity == entities.AlertSeverity.LOW


class TestParseDatadogWebhook:
    def test_parse_triggered_alert(self):
        payload = {
            "id": "12345",
            "title": "[Triggered] High CPU on web-01",
            "body": "CPU usage exceeded threshold for 10 minutes",
            "priority": "P1",
            "tags": "service:api,env:prod",
            "date": 1704067200,
            "alert_transition": "Triggered",
        }
        alert = pagerduty.parse_datadog_webhook(payload)

        assert alert is not None
        assert alert.id == "12345"
        assert alert.source == "datadog"
        assert alert.severity == entities.AlertSeverity.CRITICAL
        assert alert.service == "api"

    def test_skip_recovered_alert(self):
        payload = {
            "id": "12345",
            "title": "[Recovered] High CPU on web-01",
            "alert_transition": "Recovered",
        }
        alert = pagerduty.parse_datadog_webhook(payload)
        assert alert is None

    def test_extract_service_from_tags(self):
        payload = {
            "id": "100",
            "title": "Test",
            "body": "Test",
            "priority": "P3",
            "tags": "env:staging,service:payment-gateway,team:billing",
            "alert_transition": "Triggered",
        }
        alert = pagerduty.parse_datadog_webhook(payload)
        assert alert is not None
        assert alert.service == "payment-gateway"

    def test_unknown_service_when_no_tag(self):
        payload = {
            "id": "200",
            "title": "Test",
            "body": "Test",
            "priority": "P2",
            "tags": "env:prod",
            "alert_transition": "Triggered",
        }
        alert = pagerduty.parse_datadog_webhook(payload)
        assert alert is not None
        assert alert.service == "unknown-service"

    def test_priority_mapping(self):
        for priority, expected_severity in [
            ("P1", entities.AlertSeverity.CRITICAL),
            ("P2", entities.AlertSeverity.HIGH),
            ("P3", entities.AlertSeverity.MEDIUM),
            ("P4", entities.AlertSeverity.LOW),
        ]:
            payload = {
                "id": "300",
                "title": "Test",
                "priority": priority,
                "alert_transition": "Triggered",
            }
            alert = pagerduty.parse_datadog_webhook(payload)
            assert alert is not None
            assert alert.severity == expected_severity, f"Failed for {priority}"
