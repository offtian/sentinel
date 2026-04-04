"""
Persist and fetch ticket review records via the databases library.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import databases

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
    query = """
        INSERT INTO ticket_review_records (
            id, ticket_id, ticket_key, suggested_response,
            sources_json, confidence_score, category,
            status, created_at, trace_id
        ) VALUES (
            :id, :ticket_id, :ticket_key, :suggested_response,
            :sources_json, :confidence_score, :category,
            :status, :created_at, :trace_id
        )
    """
    await db.execute(
        query=query,
        values={
            "id": row_id,
            "ticket_id": ticket_id,
            "ticket_key": ticket_key,
            "suggested_response": suggested_response,
            "sources_json": sources_json,
            "confidence_score": confidence_score,
            "category": category,
            "status": entities.ReviewStatus.DRAFTED.value,
            "created_at": created_at,
            "trace_id": trace_id,
        },
    )
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
    query = """
        SELECT id, ticket_id, ticket_key, suggested_response,
               sources_json, confidence_score, category,
               status, created_at, reviewed_at, trace_id
        FROM ticket_review_records
        WHERE id = :id
    """
    row = await db.fetch_one(
        query=query,
        values={"id": record_id},
    )
    if row is None:
        return None
    return dict(row)


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
    query = """
        SELECT id, ticket_id, ticket_key, suggested_response,
               sources_json, confidence_score, category,
               status, created_at, reviewed_at, trace_id
        FROM ticket_review_records
        WHERE ticket_key = :ticket_key
        ORDER BY created_at DESC
    """
    rows = await db.fetch_all(
        query=query,
        values={"ticket_key": ticket_key},
    )
    return [dict(row) for row in rows]


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
    query = """
        UPDATE ticket_review_records
        SET status = :status, reviewed_at = now()
        WHERE id = :id
    """
    await db.execute(
        query=query,
        values={
            "id": record_id,
            "status": status,
        },
    )
    logs.log_event(
        "ticket_review_status_updated",
        params={
            "record_id": str(record_id),
            "status": status,
        },
    )


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
    query = """
        SELECT status, COUNT(*) AS count
        FROM ticket_review_records
        GROUP BY status
    """
    rows = await db.fetch_all(query=query, values={})
    counts_from_db = {row["status"]: row["count"] for row in rows}
    return {status.value: counts_from_db.get(status.value, 0) for status in entities.ReviewStatus}
