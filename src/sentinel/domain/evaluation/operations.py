"""
Write operations for evaluation and comparison run records.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases
from sqlalchemy import insert

from sentinel.data import evaluation_models


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
    query = insert(evaluation_models.ComparisonRunRecord).values(
        id=row_id,
        investigation_record_id=investigation_record_id,
        baseline_adapter=baseline_adapter,
        challenger_adapter=challenger_adapter,
        baseline_result_json=baseline_result_json,
        challenger_result_json=challenger_result_json,
        comparison_result_json=comparison_result_json,
        baseline_duration_ms=baseline_duration_ms,
        challenger_duration_ms=challenger_duration_ms,
    )
    await db.execute(query)
    return row_id


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
    query = insert(evaluation_models.EvalRunRecord).values(
        id=row_id,
        dataset_name=dataset_name,
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        average_score=average_score,
        results_json=results_json,
        run_duration_ms=run_duration_ms,
    )
    await db.execute(query)
    return row_id
