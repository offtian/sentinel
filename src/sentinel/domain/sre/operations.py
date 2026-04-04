"""
Write operations for investigation records.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import databases
import sqlalchemy as sa

from sentinel.data import models
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
    created_at = datetime.now(tz=UTC)
    query = sa.insert(models.InvestigationRecord).values(
        id=row_id,
        alert_source=alert_source,
        alert_id=alert_id,
        alert_title=alert_title,
        severity=severity,
        service=service,
        status=status,
        root_cause=root_cause,
        remediation=remediation,
        confidence_score=confidence_score,
        findings_json=findings_json,
        started_at=started_at,
        completed_at=completed_at,
        trace_id=trace_id,
        created_at=created_at,
    )
    await db.execute(query)
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
