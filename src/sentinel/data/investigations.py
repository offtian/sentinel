"""
Persist and fetch investigation records via the databases library.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import databases

from sentinel.utils import logs


async def persist_investigation(
    *,
    db: databases.Database,
    alert_source: str,
    alert_id: str,
    alert_title: str,
    severity: str,
    service: str,
    status: str = "completed",
    root_cause: str | None = None,
    remediation: str | None = None,
    confidence_score: float | None = None,
    findings_json: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    trace_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Insert an investigation record.

    :param db: The async database connection.
    :param alert_source: Source system that raised the alert (e.g. "pagerduty").
    :param alert_id: Identifier for the alert in the source system.
    :param alert_title: Human-readable alert title.
    :param severity: Alert severity level (e.g. "critical", "warning").
    :param service: Name of the service under investigation.
    :param status: Investigation status, defaults to "completed".
    :param root_cause: Identified root cause text, if available.
    :param remediation: Suggested remediation steps, if available.
    :param confidence_score: LLM confidence score for the findings.
    :param findings_json: Full structured findings payload.
    :param started_at: Timestamp when the investigation began.
    :param completed_at: Timestamp when the investigation finished.
    :param trace_id: Optional trace correlation UUID.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    created_at = datetime.now(tz=None)
    query = """
        INSERT INTO investigation_records (
            id, alert_source, alert_id, alert_title, severity, service,
            status, root_cause, remediation, confidence_score, findings_json,
            started_at, completed_at, created_at, trace_id
        ) VALUES (
            :id, :alert_source, :alert_id, :alert_title, :severity, :service,
            :status, :root_cause, :remediation, :confidence_score, :findings_json,
            :started_at, :completed_at, :created_at, :trace_id
        )
    """
    await db.execute(
        query=query,
        values={
            "id": row_id,
            "alert_source": alert_source,
            "alert_id": alert_id,
            "alert_title": alert_title,
            "severity": severity,
            "service": service,
            "status": status,
            "root_cause": root_cause,
            "remediation": remediation,
            "confidence_score": confidence_score,
            "findings_json": findings_json,
            "started_at": started_at,
            "completed_at": completed_at,
            "created_at": created_at,
            "trace_id": trace_id,
        },
    )
    logs.log_event(
        "investigation_persisted",
        params={
            "record_id": str(row_id),
            "alert_source": alert_source,
            "alert_id": alert_id,
            "service": service,
            "status": status,
        },
    )
    return row_id


async def fetch_investigation(
    *,
    db: databases.Database,
    record_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch a single investigation record by its primary key.

    :param db: The async database connection.
    :param record_id: UUID primary key of the investigation record.
    :returns: Row dict if found, or None.
    """
    query = """
        SELECT id, alert_source, alert_id, alert_title, severity, service,
               status, root_cause, remediation, confidence_score, findings_json,
               started_at, completed_at, created_at, trace_id
        FROM investigation_records
        WHERE id = :id
    """
    row = await db.fetch_one(
        query=query,
        values={"id": record_id},
    )
    if row is None:
        return None
    return dict(row)


async def fetch_investigations_by_alert_id(
    *,
    db: databases.Database,
    alert_id: str,
) -> list[dict[str, Any]]:
    """
    Fetch all investigation records for a given alert_id.

    :param db: The async database connection.
    :param alert_id: The alert identifier to filter by.
    :returns: List of row dicts ordered by created_at descending.
    """
    query = """
        SELECT id, alert_source, alert_id, alert_title, severity, service,
               status, root_cause, remediation, confidence_score, findings_json,
               started_at, completed_at, created_at, trace_id
        FROM investigation_records
        WHERE alert_id = :alert_id
        ORDER BY created_at DESC
    """
    rows = await db.fetch_all(
        query=query,
        values={"alert_id": alert_id},
    )
    return [dict(row) for row in rows]


async def fetch_investigations_for_service(
    *,
    db: databases.Database,
    service: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Fetch recent investigation records for a given service.

    :param db: The async database connection.
    :param service: Service name to filter by.
    :param limit: Maximum rows to return.
    :returns: List of row dicts ordered by created_at descending.
    """
    query = """
        SELECT id, alert_source, alert_id, alert_title, severity, service,
               status, root_cause, remediation, confidence_score, findings_json,
               started_at, completed_at, created_at, trace_id
        FROM investigation_records
        WHERE service = :service
        ORDER BY created_at DESC
        LIMIT :limit
    """
    rows = await db.fetch_all(
        query=query,
        values={"service": service, "limit": limit},
    )
    return [dict(row) for row in rows]
