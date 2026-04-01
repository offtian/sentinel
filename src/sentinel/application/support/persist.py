from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from sentinel.data import models
from sentinel.domain.support.entities import ReviewStatus
from sentinel.utils import logs


async def save_ticket_review(
    session: AsyncSession,
    *,
    ticket_id: str,
    ticket_key: str,
    suggested_response: str,
    sources_json: dict[str, Any] | None = None,
    confidence_score: float | None = None,
    category: str | None = None,
) -> models.TicketReviewRecord:
    """Save a completed ticket review to the database."""
    record = models.TicketReviewRecord(
        id=uuid.uuid4(),
        ticket_id=ticket_id,
        ticket_key=ticket_key,
        suggested_response=suggested_response,
        sources_json=sources_json,
        confidence_score=confidence_score,
        category=category,
        status=ReviewStatus.DRAFTED.value,
    )

    session.add(record)
    await session.commit()
    await session.refresh(record)

    logs.log_event(
        "ticket_review_persisted",
        params={"record_id": str(record.id), "ticket_key": ticket_key},
    )

    return record


async def get_ticket_review(
    session: AsyncSession,
    *,
    record_id: uuid.UUID,
) -> models.TicketReviewRecord | None:
    """Fetch a ticket review record by its ID."""
    result = await session.execute(
        select(models.TicketReviewRecord).where(col(models.TicketReviewRecord.id) == record_id)
    )
    return result.scalar_one_or_none()


async def get_reviews_for_ticket(
    session: AsyncSession,
    *,
    ticket_key: str,
) -> list[models.TicketReviewRecord]:
    """Fetch all reviews for a specific ticket."""
    result = await session.execute(
        select(models.TicketReviewRecord)
        .where(col(models.TicketReviewRecord.ticket_key) == ticket_key)
        .order_by(col(models.TicketReviewRecord.created_at).desc())
    )
    return list(result.scalars().all())


async def get_review_stats(
    session: AsyncSession,
) -> dict[str, int]:
    """
    Return counts of ticket reviews grouped by status.

    Useful for tracking acceptance rates over time.
    """
    result = await session.execute(
        select(
            models.TicketReviewRecord.status,
            func.count(col(models.TicketReviewRecord.id)),
        ).group_by(models.TicketReviewRecord.status)
    )
    rows = result.all()
    db_counts: dict[str, int] = {str(row[0]): int(row[1]) for row in rows}
    return {status.value: db_counts.get(status.value, 0) for status in ReviewStatus}


async def update_review_status(
    session: AsyncSession,
    *,
    record_id: uuid.UUID,
    status: str,
) -> models.TicketReviewRecord | None:
    """Update the status of a ticket review (e.g. accepted, rejected, modified)."""
    record = await get_ticket_review(session, record_id=record_id)
    if record is None:
        return None

    record.status = status
    record.reviewed_at = datetime.now(tz=UTC)

    session.add(record)
    await session.commit()
    await session.refresh(record)

    logs.log_event(
        "ticket_review_status_updated",
        params={"record_id": str(record.id), "status": status},
    )

    return record
