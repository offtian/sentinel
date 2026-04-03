"""
Persist and fetch evaluation run records via the databases library.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases


async def persist_eval_run(
    *,
    db: databases.Database,
    dataset_name: str,
    total_cases: int,
    passed_cases: int,
    failed_cases: int,
    average_score: float | None,
    results_json: dict[str, Any],
    run_duration_ms: int,
) -> uuid.UUID:
    """
    Insert an evaluation run record.

    :param db: The async database connection.
    :param dataset_name: Name of the evaluation dataset.
    :param total_cases: Total test cases evaluated.
    :param passed_cases: Cases that passed all assertions.
    :param failed_cases: Cases with at least one failing assertion.
    :param average_score: Mean score across all cases (nullable).
    :param results_json: Full per-case results payload.
    :param run_duration_ms: Total evaluation run duration.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    query = """
        INSERT INTO eval_runs (
            id, dataset_name, total_cases, passed_cases, failed_cases,
            average_score, results_json, run_duration_ms
        ) VALUES (
            :id, :dataset_name, :total_cases, :passed_cases, :failed_cases,
            :average_score, :results_json, :run_duration_ms
        )
    """
    await db.execute(
        query=query,
        values={
            "id": row_id,
            "dataset_name": dataset_name,
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
            "average_score": average_score,
            "results_json": results_json,
            "run_duration_ms": run_duration_ms,
        },
    )
    return row_id


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
    query = """
        SELECT id, dataset_name, total_cases, passed_cases, failed_cases,
               average_score, run_duration_ms, created_at
        FROM eval_runs
        WHERE dataset_name = :dataset_name
        ORDER BY created_at DESC
        LIMIT :limit
    """
    rows = await db.fetch_all(
        query=query,
        values={"dataset_name": dataset_name, "limit": limit},
    )
    return [dict(row._mapping) for row in rows]
