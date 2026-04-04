"""
Read operations for ticket review records.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases
import sqlalchemy as sa
from sqlalchemy import select
from sqlmodel import col

from sentinel.data import models
from sentinel.domain.support import entities


async def fetch_ticket_review(
    *,
    db: databases.Database,
    record_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch a single ticket review record by its primary key.

    :param db: The async database connection.
    :param record_id: UUID primary key of the ticket review record.
    :returns: Row dict if found, or None.
    """
    query = select(models.TicketReviewRecord).where(col(models.TicketReviewRecord.id) == record_id)
    row = await db.fetch_one(query)
    if row is None:
        return None
    return dict(row._mapping)  # noqa: SLF001


async def fetch_reviews_for_ticket(
    *,
    db: databases.Database,
    ticket_key: str,
) -> list[dict[str, Any]]:
    """
    Fetch all ticket review records for a given ticket key.

    :param db: The async database connection.
    :param ticket_key: The human-readable ticket key to filter by.
    :returns: List of row dicts ordered by created_at descending.
    """
    query = (
        select(models.TicketReviewRecord)
        .where(col(models.TicketReviewRecord.ticket_key) == ticket_key)
        .order_by(col(models.TicketReviewRecord.created_at).desc())
    )
    rows = await db.fetch_all(query)
    return [dict(row._mapping) for row in rows]  # noqa: SLF001


async def fetch_review_stats(
    *,
    db: databases.Database,
) -> dict[str, int]:
    """
    Return a count of ticket review records grouped by status.

    :param db: The async database connection.
    :returns: Dict mapping each ReviewStatus value to its record count.
              All ReviewStatus values are included, defaulting to 0 if absent.
    """
    query = sa.select(
        col(models.TicketReviewRecord.status),
        sa.func.count(col(models.TicketReviewRecord.id)).label("count"),
    ).group_by(col(models.TicketReviewRecord.status))
    rows = await db.fetch_all(query)
    counts_from_db = {row._mapping["status"]: row._mapping["count"] for row in rows}  # noqa: SLF001
    return {status.value: counts_from_db.get(status.value, 0) for status in entities.ReviewStatus}
