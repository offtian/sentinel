"""
Write operations for pipeline tracing records.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import databases
import sqlalchemy as sa
from sqlmodel import col

from sentinel.data.sql import tracing
from sentinel.utils import logs


async def persist_pipeline_run(
    *,
    db: databases.Database,
    trace_id: uuid.UUID,
    pipeline_type: str,
    job_request_id: uuid.UUID | None = None,
    started_at: datetime,
    input_json: dict[str, Any] | None = None,
    input_hash: str | None = None,
    model_ids_json: list[str] | None = None,
    mcp_endpoints_json: list[str] | None = None,
    skill_activations_json: list[dict[str, str]] | None = None,
    prompt_version: str | None = None,
    prompt_sha256: str | None = None,
    prompt_text: str | None = None,
    agent_prompts_json: list[dict[str, str]] | None = None,
) -> uuid.UUID:
    """
    Insert a pipeline_runs row with status "running".

    :param db: The async database connection.
    :param trace_id: Correlation UUID for the trace.
    :param pipeline_type: Name of the pipeline (e.g. "investigation").
    :param job_request_id: Optional job request UUID to correlate with job queue.
    :param started_at: Timestamp when the pipeline started.
    :param input_json: Optional structured input payload.
    :param input_hash: Deterministic SHA-256 of the canonical input.
    :param model_ids_json: LLM model identifiers used during this run.
    :param mcp_endpoints_json: MCP server endpoints available to the pipeline.
    :param skill_activations_json: Skills activated for the pipeline agents.
    :param prompt_version: Git-SHA-prefixed version tag of the system prompt.
    :param prompt_sha256: Content hash of the system prompt text.
    :param prompt_text: Full rendered system prompt text for replay.
    :param agent_prompts_json: Per-agent prompt metadata for multi-agent pipelines.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    created_at = datetime.now(tz=UTC)
    query = sa.insert(tracing.PipelineRunRecord).values(
        id=row_id,
        trace_id=trace_id,
        pipeline_type=pipeline_type,
        job_request_id=job_request_id,
        status="running",
        input_json=input_json,
        output_json=None,
        error_message=None,
        started_at=started_at,
        completed_at=None,
        duration_ms=None,
        created_at=created_at,
        input_hash=input_hash,
        model_ids_json=model_ids_json,
        mcp_endpoints_json=mcp_endpoints_json,
        skill_activations_json=skill_activations_json,
        prompt_version=prompt_version,
        prompt_sha256=prompt_sha256,
        prompt_text=prompt_text,
        agent_prompts_json=agent_prompts_json,
    )
    await db.execute(query)
    logs.log_event(
        "pipeline_run_persisted",
        params={
            "run_id": str(row_id),
            "trace_id": str(trace_id),
            "pipeline_type": pipeline_type,
        },
    )
    return row_id


async def complete_pipeline_run(
    *,
    db: databases.Database,
    run_id: uuid.UUID,
    status: str,
    output_json: dict[str, Any] | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    final_reply: dict[str, Any] | None = None,
    total_token_usage_json: dict[str, Any] | None = None,
    replay_bundle_json: dict[str, Any] | None = None,
    replay_bundle_sha: str | None = None,
) -> None:
    """
    Update a pipeline_runs row to its final status.

    :param db: The async database connection.
    :param run_id: UUID primary key of the pipeline run to update.
    :param status: Terminal status value (e.g. "completed", "failed").
    :param output_json: Optional structured output payload.
    :param error_message: Optional error description if the run failed.
    :param duration_ms: Wall-clock duration of the run in milliseconds.
    :param final_reply: Optional structured final reply payload for replay.
    :param total_token_usage_json: Optional aggregate token usage and cost across all agent calls.
    :param replay_bundle_json: Optional RFC §3.8 ReplayBundle as canonical JSON dict.
    :param replay_bundle_sha: Optional SHA-256 over the canonical bundle JSON.
    """
    completed_at = datetime.now(tz=UTC)
    query = (
        sa.update(tracing.PipelineRunRecord)
        .where(col(tracing.PipelineRunRecord.id) == run_id)
        .values(
            status=status,
            output_json=output_json,
            error_message=error_message,
            completed_at=completed_at,
            duration_ms=duration_ms,
            final_reply=final_reply,
            total_token_usage_json=total_token_usage_json,
            replay_bundle_json=replay_bundle_json,
            replay_bundle_sha=replay_bundle_sha,
        )
    )
    await db.execute(query)
    logs.log_event(
        "pipeline_run_completed",
        params={
            "run_id": str(run_id),
            "status": status,
            "replay_bundle_sha": replay_bundle_sha,
        },
    )


async def persist_node_execution(
    *,
    db: databases.Database,
    trace_id: uuid.UUID,
    pipeline_run_id: uuid.UUID,
    node_name: str,
    node_order: int,
    started_at: datetime,
    input_json: dict[str, Any] | None = None,
) -> uuid.UUID:
    """
    Insert a node_executions row with status "running".

    :param db: The async database connection.
    :param trace_id: Correlation UUID for the trace.
    :param pipeline_run_id: UUID of the parent pipeline run.
    :param node_name: Name of the pipeline node (e.g. "ClassifyAlert").
    :param node_order: Zero-based ordinal position of the node in the pipeline.
    :param started_at: Timestamp when the node started executing.
    :param input_json: Optional structured input payload for this node.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    created_at = datetime.now(tz=UTC)
    query = sa.insert(tracing.NodeExecutionRecord).values(
        id=row_id,
        trace_id=trace_id,
        pipeline_run_id=pipeline_run_id,
        node_name=node_name,
        node_order=node_order,
        status="running",
        input_json=input_json,
        output_json=None,
        error_message=None,
        started_at=started_at,
        completed_at=None,
        duration_ms=None,
        created_at=created_at,
    )
    await db.execute(query)
    logs.log_event(
        "node_execution_persisted",
        params={
            "node_id": str(row_id),
            "trace_id": str(trace_id),
            "pipeline_run_id": str(pipeline_run_id),
            "node_name": node_name,
            "node_order": node_order,
        },
    )
    return row_id


async def complete_node_execution(
    *,
    db: databases.Database,
    node_id: uuid.UUID,
    status: str,
    output_json: dict[str, Any] | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """
    Update a node_executions row to its final status.

    :param db: The async database connection.
    :param node_id: UUID primary key of the node execution to update.
    :param status: Terminal status value (e.g. "completed", "failed").
    :param output_json: Optional structured output payload.
    :param error_message: Optional error description if the node failed.
    :param duration_ms: Wall-clock duration of the node in milliseconds.
    """
    completed_at = datetime.now(tz=UTC)
    query = (
        sa.update(tracing.NodeExecutionRecord)
        .where(col(tracing.NodeExecutionRecord.id) == node_id)
        .values(
            status=status,
            output_json=output_json,
            error_message=error_message,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
    )
    await db.execute(query)
    logs.log_event(
        "node_execution_completed",
        params={
            "node_id": str(node_id),
            "status": status,
        },
    )


async def persist_agent_call(
    *,
    db: databases.Database,
    trace_id: uuid.UUID,
    node_execution_id: uuid.UUID,
    agent_name: str,
    model_id: str = "",
    messages_json: list[dict[str, Any]] | None = None,
    token_usage_json: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    started_at: datetime,
    completed_at: datetime | None = None,
) -> uuid.UUID:
    """
    Insert an agent_calls row.

    :param db: The async database connection.
    :param trace_id: Correlation UUID for the trace.
    :param node_execution_id: UUID of the parent node execution.
    :param agent_name: Name of the PydanticAI agent (e.g. "AlertClassifier").
    :param model_id: LLM model identifier used for this call.
    :param messages_json: Optional list of message dicts from the LLM exchange.
    :param token_usage_json: Optional token usage metrics from the LLM provider.
    :param duration_ms: Wall-clock duration of the agent call in milliseconds.
    :param started_at: Timestamp when the agent call began.
    :param completed_at: Optional timestamp when the agent call finished.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    created_at = datetime.now(tz=UTC)
    query = sa.insert(tracing.AgentCallRecord).values(
        id=row_id,
        trace_id=trace_id,
        node_execution_id=node_execution_id,
        agent_name=agent_name,
        model_id=model_id,
        messages_json=messages_json,
        token_usage_json=token_usage_json,
        duration_ms=duration_ms,
        started_at=started_at,
        completed_at=completed_at,
        created_at=created_at,
    )
    await db.execute(query)
    logs.log_event(
        "agent_call_persisted",
        params={
            "call_id": str(row_id),
            "trace_id": str(trace_id),
            "node_execution_id": str(node_execution_id),
            "agent_name": agent_name,
            "model_id": model_id,
        },
    )
    return row_id
