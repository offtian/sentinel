"""
Read operations for investigation records.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases
from sqlalchemy import select
from sqlmodel import col

from sentinel.data.sql import investigations


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
    query = select(investigations.InvestigationRecord).where(
        col(investigations.InvestigationRecord.id) == record_id
    )
    row = await db.fetch_one(query)
    if row is None:
        return None
    return dict(row._mapping)  # noqa: SLF001


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
    query = (
        select(investigations.InvestigationRecord)
        .where(col(investigations.InvestigationRecord.alert_id) == alert_id)
        .order_by(col(investigations.InvestigationRecord.created_at).desc())
    )
    rows = await db.fetch_all(query)
    return [dict(row._mapping) for row in rows]  # noqa: SLF001


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
    query = (
        select(investigations.InvestigationRecord)
        .where(col(investigations.InvestigationRecord.service) == service)
        .order_by(col(investigations.InvestigationRecord.created_at).desc())
        .limit(limit)
    )
    rows = await db.fetch_all(query)
    return [dict(row._mapping) for row in rows]  # noqa: SLF001
