"""
Read operations for job records.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases
from sqlalchemy import select
from sqlmodel import col

from sentinel.data import job_models


async def fetch_job(
    *,
    db: databases.Database,
    job_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch a single job request by its primary key.

    :param db: The async database connection.
    :param job_id: UUID primary key of the job request.
    :returns: Row dict if found, or None.
    """
    query = select(job_models.JobRequestRecord).where(
        col(job_models.JobRequestRecord.id) == job_id
    )
    row = await db.fetch_one(query)
    if row is None:
        return None
    return dict(row._mapping)  # noqa: SLF001
