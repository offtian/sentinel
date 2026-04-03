"""
Persist and fetch comparison run records via the databases library.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases


async def persist_comparison_run(
    *,
    db: databases.Database,
    investigation_record_id: uuid.UUID,
    baseline_adapter: str,
    challenger_adapter: str,
    baseline_result_json: dict[str, Any],
    challenger_result_json: dict[str, Any],
    comparison_result_json: dict[str, Any],
    baseline_duration_ms: int,
    challenger_duration_ms: int,
) -> uuid.UUID:
    """
    Insert a comparison run record.

    :param db: The async database connection.
    :param investigation_record_id: FK to the investigation_records table.
    :param baseline_adapter: Name of the baseline adapter (e.g. "holmes").
    :param challenger_adapter: Name of the challenger adapter (e.g. "native_k8s").
    :param baseline_result_json: Serialised baseline InvestigationResult.
    :param challenger_result_json: Serialised challenger InvestigationResult.
    :param comparison_result_json: Serialised ComparisonResult with metrics.
    :param baseline_duration_ms: Baseline investigation duration.
    :param challenger_duration_ms: Challenger investigation duration.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    query = """
        INSERT INTO comparison_runs (
            id, investigation_record_id, baseline_adapter, challenger_adapter,
            baseline_result_json, challenger_result_json, comparison_result_json,
            baseline_duration_ms, challenger_duration_ms
        ) VALUES (
            :id, :investigation_record_id, :baseline_adapter, :challenger_adapter,
            :baseline_result_json, :challenger_result_json, :comparison_result_json,
            :baseline_duration_ms, :challenger_duration_ms
        )
    """
    await db.execute(
        query=query,
        values={
            "id": row_id,
            "investigation_record_id": investigation_record_id,
            "baseline_adapter": baseline_adapter,
            "challenger_adapter": challenger_adapter,
            "baseline_result_json": baseline_result_json,
            "challenger_result_json": challenger_result_json,
            "comparison_result_json": comparison_result_json,
            "baseline_duration_ms": baseline_duration_ms,
            "challenger_duration_ms": challenger_duration_ms,
        },
    )
    return row_id


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
    :returns: List of row dicts.
    """
    query = """
        SELECT id, investigation_record_id, baseline_adapter, challenger_adapter,
               baseline_duration_ms, challenger_duration_ms, created_at
        FROM comparison_runs
        WHERE investigation_record_id = :investigation_record_id
        ORDER BY created_at DESC
        LIMIT :limit
    """
    rows = await db.fetch_all(
        query=query,
        values={
            "investigation_record_id": investigation_record_id,
            "limit": limit,
        },
    )
    return [dict(row._mapping) for row in rows]
