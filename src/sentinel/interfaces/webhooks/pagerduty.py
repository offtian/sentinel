from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sentinel.domain.sre import entities
from sentinel.utils import logs


def parse_pagerduty_webhook(payload: dict[str, Any]) -> entities.Alert | None:
    """
    Parse a PagerDuty V3 webhook event into an Alert entity.

    PagerDuty V3 webhook events have a structure like:
    {
        "event": {
            "event_type": "incident.triggered",
            "data": {
                "id": "P123ABC",
                "title": "High CPU usage on web-server-01",
                "urgency": "high",
                "service": {"summary": "Production API"},
                "body": {"details": "CPU usage exceeded 90%..."}
            }
        }
    }

    Returns None for events we don't want to investigate.
    """
    event = payload.get("event", {})
    event_type = event.get("event_type", "")

    # Only process triggered and escalated incidents
    if event_type not in ("incident.triggered", "incident.escalated"):
        logs.log_event(
            "pagerduty_event_skipped",
            params={"event_type": event_type},
        )
        return None

    data = event.get("data", {})
    incident_id = data.get("id", "")
    title = data.get("title", "Unknown alert")
    urgency = data.get("urgency", "low")
    service_info = data.get("service", {})
    service_name = service_info.get("summary", "unknown-service")
    body = data.get("body", {})
    description = body.get("details", title)

    severity_map = {
        "high": entities.AlertSeverity.HIGH,
        "low": entities.AlertSeverity.LOW,
    }
    severity = severity_map.get(urgency, entities.AlertSeverity.MEDIUM)

    triggered_at_str = data.get("created_at")
    triggered_at = (
        datetime.fromisoformat(triggered_at_str)
        if triggered_at_str
        else datetime.now(tz=UTC)
    )

    return entities.Alert(
        id=incident_id,
        source="pagerduty",
        title=title,
        description=str(description),
        severity=severity,
        service=service_name,
        triggered_at=triggered_at,
        raw_payload=payload,
    )


def parse_datadog_webhook(payload: dict[str, Any]) -> entities.Alert | None:
    """
    Parse a Datadog webhook payload into an Alert entity.

    Datadog webhooks have a structure like:
    {
        "id": "12345",
        "title": "[Triggered] High CPU on web-01",
        "body": "CPU usage exceeded threshold...",
        "priority": "P1",
        "tags": "service:api,env:prod",
        "date": 1234567890,
        "alert_transition": "Triggered"
    }

    Returns None for recovery events.
    """
    alert_transition = payload.get("alert_transition", "")

    if alert_transition.lower() in ("recovered", "no data"):
        logs.log_event(
            "datadog_event_skipped",
            params={"transition": alert_transition},
        )
        return None

    alert_id = str(payload.get("id", ""))
    title = payload.get("title", "Unknown Datadog alert")
    body = payload.get("body", title)
    priority = payload.get("priority", "").upper()

    # Extract service from tags
    tags = payload.get("tags", "")
    service = "unknown-service"
    if isinstance(tags, str):
        for tag in tags.split(","):
            if tag.strip().startswith("service:"):
                service = tag.strip().split(":", 1)[1]
                break

    severity_map = {
        "P1": entities.AlertSeverity.CRITICAL,
        "P2": entities.AlertSeverity.HIGH,
        "P3": entities.AlertSeverity.MEDIUM,
        "P4": entities.AlertSeverity.LOW,
    }
    severity = severity_map.get(priority, entities.AlertSeverity.MEDIUM)

    date_epoch = payload.get("date")
    triggered_at = (
        datetime.fromtimestamp(int(date_epoch), tz=UTC)
        if date_epoch
        else datetime.now(tz=UTC)
    )

    return entities.Alert(
        id=alert_id,
        source="datadog",
        title=title,
        description=str(body),
        severity=severity,
        service=service,
        triggered_at=triggered_at,
        raw_payload=payload,
    )
