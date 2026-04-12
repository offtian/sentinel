"""
Read operations for pipeline tracing records.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import databases
from sqlalchemy import select
from sqlmodel import col

from sentinel.data import tracing_models
from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.domain.pipeline import types as pipeline_types


_EXCLUDED_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "received_at",
        "now",
        "run_id",
        "trace_id",
        "pipeline_run_id",
        "created_at",
        "updated_at",
    }
)


def canonical_input_hash(*, payload: dict[str, Any]) -> str:
    """
    Return a deterministic SHA-256 hex digest of *payload*.

    Keys in ``_EXCLUDED_KEYS`` (timestamps, trace IDs) are stripped so that
    two runs of the same alert at different times produce the same hash.
    """
    filtered = {k: v for k, v in payload.items() if k not in _EXCLUDED_KEYS}
    canonical = json.dumps(filtered, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


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


async def fetch_replay_bundle(
    *,
    db: databases.Database,
    run_id: uuid.UUID,
) -> pipeline_types.ReplayBundle:
    """
    Return an immutable snapshot of a pipeline run for reproducibility.

    :param db: The async database connection.
    :param run_id: Primary key of the pipeline run record.
    :returns: A fully-populated ``ReplayBundle``.
    :raises pipeline_errors.ReplayBundleNotFoundError: if no row matches *run_id*.
    """
    query = select(tracing_models.PipelineRunRecord).where(
        col(tracing_models.PipelineRunRecord.id) == run_id
    )
    row = await db.fetch_one(query)
    if row is None:
        raise pipeline_errors.ReplayBundleNotFoundError(run_id)
    mapping = dict(row._mapping)  # noqa: SLF001
    return pipeline_types.ReplayBundle(
        run_id=mapping["id"],
        pipeline_type=mapping["pipeline_type"],
        started_at=mapping["started_at"],
        completed_at=mapping.get("completed_at"),
        prompt_version=mapping.get("prompt_version"),
        prompt_sha256=mapping.get("prompt_sha256"),
        prompt_text=mapping.get("prompt_text"),
        input_hash=mapping.get("input_hash"),
        model_ids=tuple(mapping.get("model_ids_json") or []),
        mcp_endpoints=tuple(mapping.get("mcp_endpoints_json") or []),
        skill_activations=tuple(mapping.get("skill_activations_json") or []),
        final_reply=mapping.get("final_reply"),
        input_payload=mapping.get("input_json"),
        agent_prompts=tuple(mapping.get("agent_prompts_json") or []),
    )
