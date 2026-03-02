from __future__ import annotations

from typing import Any

import fastapi

from sentinel import _config
from sentinel.domain.sre import holmes_adapter
from sentinel.interfaces.graphs import common, sre_investigation
from sentinel.interfaces.webhooks import pagerduty
from sentinel.utils import logs


router = fastapi.APIRouter(prefix="/sre", tags=["sre"])


@router.post("/webhooks/pagerduty")
async def handle_pagerduty_webhook(
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """Receive and process PagerDuty V3 webhook events."""
    alert = pagerduty.parse_pagerduty_webhook(payload)
    if alert is None:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "skipped", "reason": "Event type not actionable"},
        )

    logs.log_event(
        "pagerduty_alert_received",
        params={"alert_id": alert.id, "title": alert.title},
    )

    if not _config.SRE_AUTO_INVESTIGATE:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "received", "alert_id": alert.id, "auto_investigate": False},
        )

    holmes = holmes_adapter.HolmesAdapter(enabled=_config.HOLMESGPT_ENABLED)
    result = await sre_investigation.investigate_alert(alert=alert, holmes=holmes)

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "status": "investigated",
            "alert_id": alert.id,
            "root_cause": result.root_cause,
            "confidence": result.confidence.total if result.confidence else None,
        },
    )


@router.post("/webhooks/datadog")
async def handle_datadog_webhook(
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """Receive and process Datadog webhook events."""
    alert = pagerduty.parse_datadog_webhook(payload)
    if alert is None:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "skipped", "reason": "Event type not actionable"},
        )

    logs.log_event(
        "datadog_alert_received",
        params={"alert_id": alert.id, "title": alert.title},
    )

    if not _config.SRE_AUTO_INVESTIGATE:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "received", "alert_id": alert.id, "auto_investigate": False},
        )

    holmes = holmes_adapter.HolmesAdapter(enabled=_config.HOLMESGPT_ENABLED)
    result = await sre_investigation.investigate_alert(alert=alert, holmes=holmes)

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "status": "investigated",
            "alert_id": alert.id,
            "root_cause": result.root_cause,
            "confidence": result.confidence.total if result.confidence else None,
        },
    )


@router.post("/investigate")
async def trigger_investigation(
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """
    Manually trigger an investigation for a given alert.

    Expects a simplified alert payload:
    {
        "id": "alert-123",
        "title": "High CPU usage",
        "description": "CPU usage exceeded 90% on web-01",
        "severity": "high",
        "service": "api-service",
        "source": "manual"
    }
    """
    from datetime import UTC, datetime

    from sentinel.domain.sre import entities

    alert = entities.Alert(
        id=payload.get("id", "manual-alert"),
        source=payload.get("source", "pagerduty"),
        title=payload.get("title", "Manual investigation"),
        description=payload.get("description", ""),
        severity=entities.AlertSeverity(payload.get("severity", "medium")),
        service=payload.get("service", "unknown"),
        triggered_at=datetime.now(tz=UTC),
        raw_payload=payload,
    )

    holmes = holmes_adapter.HolmesAdapter(enabled=_config.HOLMESGPT_ENABLED)
    result = await sre_investigation.investigate_alert(alert=alert, holmes=holmes)

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "status": "investigated",
            "alert_id": alert.id,
            "root_cause": result.root_cause,
            "remediation": result.remediation,
            "confidence": result.confidence.total if result.confidence else None,
            "findings_summary": result.findings_summary,
        },
    )
