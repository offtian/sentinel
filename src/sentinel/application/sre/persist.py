from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from sentinel.data import models
from sentinel.utils import logs


async def save_investigation(
    session: AsyncSession,
    *,
    alert_source: str,
    alert_id: str,
    alert_title: str,
    severity: str,
    service: str,
    root_cause: str | None = None,
    remediation: str | None = None,
    confidence_score: float | None = None,
    findings_json: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> models.InvestigationRecord:
    """Save a completed investigation to the database."""
    record = models.InvestigationRecord(
        id=uuid.uuid4(),
        alert_source=alert_source,
        alert_id=alert_id,
        alert_title=alert_title,
        severity=severity,
        service=service,
        status="completed",
        root_cause=root_cause,
        remediation=remediation,
        confidence_score=confidence_score,
        findings_json=findings_json,
        started_at=started_at or datetime.now(tz=UTC),
        completed_at=completed_at or datetime.now(tz=UTC),
    )

    session.add(record)
    await session.commit()
    await session.refresh(record)

    logs.log_event(
        "investigation_persisted",
        params={"record_id": str(record.id), "alert_id": alert_id},
    )

    return record


async def get_investigation(
    session: AsyncSession,
    *,
    record_id: uuid.UUID,
) -> models.InvestigationRecord | None:
    """Fetch an investigation record by its ID."""
    result = await session.execute(
        select(models.InvestigationRecord).where(col(models.InvestigationRecord.id) == record_id)
    )
    return result.scalar_one_or_none()


async def get_investigations_for_service(
    session: AsyncSession,
    *,
    service: str,
    limit: int = 10,
) -> list[models.InvestigationRecord]:
    """Fetch recent investigations for a given service."""
    result = await session.execute(
        select(models.InvestigationRecord)
        .where(col(models.InvestigationRecord.service) == service)
        .order_by(col(models.InvestigationRecord.created_at).desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_investigations_by_alert_id(
    session: AsyncSession,
    *,
    alert_id: str,
) -> list[models.InvestigationRecord]:
    """Fetch all investigations for a specific alert."""
    result = await session.execute(
        select(models.InvestigationRecord)
        .where(col(models.InvestigationRecord.alert_id) == alert_id)
        .order_by(col(models.InvestigationRecord.created_at).desc())
    )
    return list(result.scalars().all())
