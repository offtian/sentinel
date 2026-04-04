from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import fastapi
from pydantic import BaseModel

from sentinel.data import db as async_db
from sentinel.domain.jobs import operations as job_ops
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import queries as sre_queries
from sentinel.interfaces.api.dependencies import require_database
from sentinel.interfaces.webhooks import pagerduty
from sentinel.settings import get_settings
from sentinel.utils import logs


router = fastapi.APIRouter(prefix="/sre", tags=["sre"])


async def _enqueue_alert(
    alert: sre_entities.Alert,
    *,
    requested_by: str,
    priority: int = 1,
) -> fastapi.responses.JSONResponse:
    """Enqueue an alert for async investigation and return 202 Accepted."""
    db = async_db.get_db()
    job_id = await job_ops.enqueue_investigation(
        db=db,
        alert_payload=alert.model_dump(mode="json"),
        requested_by=requested_by,
        alert_id=alert.id,
        priority=priority,
    )

    return fastapi.responses.JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": str(job_id),
            "alert_id": alert.id,
        },
    )


async def _handle_webhook(
    *,
    payload: dict[str, Any],
    parse_fn: Callable[[dict[str, Any]], sre_entities.Alert | None],
    source: str,
) -> fastapi.responses.JSONResponse:
    """Shared handler for all alert-source webhooks (PagerDuty, Datadog, etc.)."""
    alert = parse_fn(payload)
    if alert is None:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "skipped", "reason": "Event type not actionable"},
        )

    logs.log_event(
        f"{source}_alert_received",
        params={"alert_id": alert.id, "title": alert.title},
    )

    if not get_settings().sre_auto_investigate:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "received", "alert_id": alert.id, "auto_investigate": False},
        )

    return await _enqueue_alert(alert, requested_by=f"webhook:{source}")


@router.post("/webhooks/pagerduty")
async def handle_pagerduty_webhook(
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """Receive PagerDuty V3 webhook events and enqueue for async investigation."""
    return await _handle_webhook(
        payload=payload,
        parse_fn=pagerduty.parse_pagerduty_webhook,
        source="pagerduty",
    )


@router.post("/webhooks/datadog")
async def handle_datadog_webhook(
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """Receive Datadog webhook events and enqueue for async investigation."""
    return await _handle_webhook(
        payload=payload,
        parse_fn=pagerduty.parse_datadog_webhook,
        source="datadog",
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
    alert = sre_entities.Alert(
        id=payload.get("id", "manual-alert"),
        source=payload.get("source", "pagerduty"),
        title=payload.get("title", "Manual investigation"),
        description=payload.get("description", ""),
        severity=sre_entities.AlertSeverity(payload.get("severity", "medium")),
        service=payload.get("service", "unknown"),
        triggered_at=datetime.now(tz=UTC),
        raw_payload=payload,
    )

    return await _enqueue_alert(alert, requested_by="api:manual")


@router.get(
    "/investigations/{investigation_id}",
    dependencies=[fastapi.Depends(require_database)],
)
async def get_investigation(
    investigation_id: uuid.UUID,
) -> fastapi.responses.JSONResponse:
    """
    Return an investigation record by its ID.

    Returns 404 if the investigation is not found.
    """
    db = async_db.get_db()
    record = await sre_queries.fetch_investigation(db=db, record_id=investigation_id)

    if record is None:
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={
                "error": "Investigation not found",
                "investigation_id": str(investigation_id),
            },
        )

    started_at = record["started_at"]
    completed_at = record["completed_at"]
    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "investigation_id": str(record["id"]),
            "alert_source": record["alert_source"],
            "alert_id": record["alert_id"],
            "alert_title": record["alert_title"],
            "severity": record["severity"],
            "service": record["service"],
            "status": record["status"],
            "root_cause": record["root_cause"],
            "remediation": record["remediation"],
            "confidence_score": record["confidence_score"],
            "findings": record["findings_json"],
            "started_at": started_at.isoformat() if started_at else None,
            "completed_at": completed_at.isoformat() if completed_at else None,
            "created_at": record["created_at"].isoformat(),
        },
    )


# ── Approval Gate ────────────────────────────────────────────────────────────

# In-memory store for pending approvals.
# Production: replace with database-backed store via Alembic migration.
_pending_approvals: dict[str, dict[str, Any]] = {}


class ApprovalAction(BaseModel):
    reviewer: str


def store_pending_approval(
    *,
    investigation_id: str,
    approval_data: dict[str, Any],
) -> None:
    """Store a pending approval for later retrieval by approve/reject endpoints."""
    _pending_approvals[investigation_id] = {
        **approval_data,
        "status": "pending",
        "requested_at": datetime.now(tz=UTC).isoformat(),
    }


def get_pending_approval(investigation_id: str) -> dict[str, Any] | None:
    """Return pending approval data, or None if not found."""
    return _pending_approvals.get(investigation_id)


def remove_pending_approval(investigation_id: str) -> None:
    """Remove a resolved approval from the pending store."""
    _pending_approvals.pop(investigation_id, None)


@router.post("/investigations/{investigation_id}/approve")
async def approve_investigation(
    investigation_id: str,
    action: ApprovalAction,
) -> fastapi.responses.JSONResponse:
    """
    Approve an investigation for publishing to external channels.

    Called by Slack interactive message handler or directly by an engineer.
    """
    pending = get_pending_approval(investigation_id)
    if not pending:
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={
                "error": "No pending approval found",
                "investigation_id": investigation_id,
            },
        )

    logs.log_event(
        "investigation.approved",
        params={
            "investigation_id": investigation_id,
            "reviewer": action.reviewer,
        },
    )

    remove_pending_approval(investigation_id)

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "investigation_id": investigation_id,
            "status": "approved",
            "reviewer": action.reviewer,
            "approved_at": datetime.now(tz=UTC).isoformat(),
        },
    )


@router.post("/investigations/{investigation_id}/reject")
async def reject_investigation(
    investigation_id: str,
    action: ApprovalAction,
) -> fastapi.responses.JSONResponse:
    """
    Reject an investigation -- findings will NOT be published.

    Called by Slack interactive message handler or directly by an engineer.
    """
    pending = get_pending_approval(investigation_id)
    if not pending:
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={
                "error": "No pending approval found",
                "investigation_id": investigation_id,
            },
        )

    logs.log_event(
        "investigation.rejected",
        params={
            "investigation_id": investigation_id,
            "reviewer": action.reviewer,
        },
    )

    remove_pending_approval(investigation_id)

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "investigation_id": investigation_id,
            "status": "rejected",
            "reviewer": action.reviewer,
            "rejected_at": datetime.now(tz=UTC).isoformat(),
        },
    )


@router.get("/investigations/{investigation_id}/approval-status")
async def get_approval_status(investigation_id: str) -> fastapi.responses.JSONResponse:
    """Check the current approval status of an investigation."""
    pending = get_pending_approval(investigation_id)
    if not pending:
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={
                "error": "No pending approval found",
                "investigation_id": investigation_id,
            },
        )

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "investigation_id": investigation_id,
            "status": pending["status"],
            "requested_at": pending["requested_at"],
        },
    )
