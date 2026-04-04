"""
Read-only queries for evaluation and comparison run records.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases
from sqlalchemy import select
from sqlmodel import col

from sentinel.data import evaluation_models


async def fetch_comparison_runs(
    *,
    db: databases.Database,
    investigation_record_id: uuid.UUID,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Fetch comparison runs for a given investigation record.

    :param db: The async database connection.
    :param investigation_record_id: FK to filter by.
    :param limit: Maximum rows to return.
    :returns: List of row dicts ordered by created_at descending.
    """
    query = (
        select(evaluation_models.ComparisonRunRecord)
        .where(
            col(evaluation_models.ComparisonRunRecord.investigation_record_id)
            == investigation_record_id
        )
        .order_by(col(evaluation_models.ComparisonRunRecord.created_at).desc())
        .limit(limit)
    )
    rows = await db.fetch_all(query)
    return [dict(row._mapping) for row in rows]  # noqa: SLF001


async def fetch_eval_runs(
    *,
    db: databases.Database,
    dataset_name: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Fetch recent evaluation runs for a dataset.

    :param db: The async database connection.
    :param dataset_name: Dataset name to filter by.
    :param limit: Maximum rows to return.
    :returns: List of row dicts ordered by created_at descending.
    """
    query = (
        select(evaluation_models.EvalRunRecord)
        .where(col(evaluation_models.EvalRunRecord.dataset_name) == dataset_name)
        .order_by(col(evaluation_models.EvalRunRecord.created_at).desc())
        .limit(limit)
    )
    rows = await db.fetch_all(query)
    return [dict(row._mapping) for row in rows]  # noqa: SLF001
