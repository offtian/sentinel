"""
Write operations for ticket review records.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import databases
from sqlalchemy import insert, update
from sqlmodel import col

from sentinel.data import models
from sentinel.domain.support import entities
from sentinel.utils import logs


async def persist_ticket_review(
    *,
    db: databases.Database,
    ticket_id: str,
    ticket_key: str,
    suggested_response: str,
    sources_json: dict[str, Any] | None = None,
    confidence_score: float | None = None,
    category: str | None = None,
    trace_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Insert a ticket review record with status "drafted".

    :param db: The async database connection.
    :param ticket_id: The external ticket identifier (e.g. Jira issue ID).
    :param ticket_key: The human-readable ticket key (e.g. "SUPPORT-42").
    :param suggested_response: The AI-generated response suggestion text.
    :param sources_json: Serialised list of documentation sources used.
    :param confidence_score: LLM confidence score for the suggestion.
    :param category: Classified ticket category, if available.
    :param trace_id: Optional trace correlation UUID.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    created_at = datetime.now(tz=UTC)
    query = insert(models.TicketReviewRecord).values(
        id=row_id,
        ticket_id=ticket_id,
        ticket_key=ticket_key,
        suggested_response=suggested_response,
        sources_json=sources_json,
        confidence_score=confidence_score,
        category=category,
        status=entities.ReviewStatus.DRAFTED.value,
        created_at=created_at,
    )
    await db.execute(query)
    logs.log_event(
        "ticket_review_persisted",
        params={
            "record_id": str(row_id),
            "ticket_id": ticket_id,
            "ticket_key": ticket_key,
            "category": category,
        },
    )
    return row_id


async def update_review_status(
    *,
    db: databases.Database,
    record_id: uuid.UUID,
    status: str,
) -> None:
    """
    Update the status of a ticket review record and set reviewed_at to now.

    :param db: The async database connection.
    :param record_id: UUID primary key of the ticket review record.
    :param status: The new status value (e.g. "accepted", "rejected", "modified").
    :returns: None.
    """
    now = datetime.now(tz=UTC)
    query = (
        update(models.TicketReviewRecord)
        .where(col(models.TicketReviewRecord.id) == record_id)
        .values(status=status, reviewed_at=now)
    )
    await db.execute(query)
    logs.log_event(
        "ticket_review_status_updated",
        params={
            "record_id": str(record_id),
            "status": status,
        },
    )
