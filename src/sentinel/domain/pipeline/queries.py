"""
Read operations for pipeline tracing records.
"""

from __future__ import annotations

import uuid
from typing import Any

import databases
from sqlalchemy import select
from sqlmodel import col

from sentinel.data import tracing_models


async def fetch_pipeline_run(
    *,
    db: databases.Database,
    trace_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch a single pipeline run record by trace_id.

    :param db: The async database connection.
    :param trace_id: Correlation UUID used to look up the run.
    :returns: Row dict if found, or None.
    """
    query = select(tracing_models.PipelineRunRecord).where(
        col(tracing_models.PipelineRunRecord.trace_id) == trace_id
    )
    row = await db.fetch_one(query)
    if row is None:
        return None
    return dict(row._mapping)  # noqa: SLF001


async def fetch_node_executions(
    *,
    db: databases.Database,
    pipeline_run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    Fetch all node execution records for a given pipeline run.

    :param db: The async database connection.
    :param pipeline_run_id: UUID of the parent pipeline run.
    :returns: List of row dicts ordered by node_order ascending.
    """
    query = (
        select(tracing_models.NodeExecutionRecord)
        .where(col(tracing_models.NodeExecutionRecord.pipeline_run_id) == pipeline_run_id)
        .order_by(col(tracing_models.NodeExecutionRecord.node_order).asc())
    )
    rows = await db.fetch_all(query)
    return [dict(row._mapping) for row in rows]  # noqa: SLF001


async def fetch_agent_calls(
    *,
    db: databases.Database,
    node_execution_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    Fetch all agent call records for a given node execution.

    :param db: The async database connection.
    :param node_execution_id: UUID of the parent node execution.
    :returns: List of row dicts ordered by started_at ascending.
    """
    query = (
        select(tracing_models.AgentCallRecord)
        .where(col(tracing_models.AgentCallRecord.node_execution_id) == node_execution_id)
        .order_by(col(tracing_models.AgentCallRecord.started_at).asc())
    )
    rows = await db.fetch_all(query)
    return [dict(row._mapping) for row in rows]  # noqa: SLF001
