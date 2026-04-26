"""
SQLModel table definitions for pipeline tracing: runs, node executions, and agent calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Text
from sqlalchemy.dialects.postgresql import JSON, JSONB
from sqlmodel import Field, SQLModel


class PipelineRunRecord(SQLModel, table=True):
    """
    Persist a single end-to-end pipeline execution.
    """

    __tablename__ = "pipeline_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    trace_id: uuid.UUID = Field(index=True)
    pipeline_type: str
    job_request_id: uuid.UUID | None = Field(default=None, index=True)
    status: str = Field(default="running")
    input_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    output_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text))
    started_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    duration_ms: int | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    # -- Replay snapshot columns (all nullable for rolling deploy) --
    input_hash: str | None = Field(default=None, max_length=64, index=True)
    model_ids_json: list[str] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    mcp_endpoints_json: list[str] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    skill_activations_json: list[dict[str, str]] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    final_reply: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    prompt_version: str | None = Field(default=None, max_length=128)
    prompt_sha256: str | None = Field(default=None, max_length=64, index=True)
    prompt_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    agent_prompts_json: list[dict[str, str]] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    total_token_usage_json: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )


class NodeExecutionRecord(SQLModel, table=True):
    """
    Persist a single pipeline node execution within a run.
    """

    __tablename__ = "node_executions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    trace_id: uuid.UUID = Field(index=True)
    pipeline_run_id: uuid.UUID = Field(index=True)
    node_name: str
    node_order: int
    status: str = Field(default="running")
    input_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    output_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text))
    started_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    duration_ms: int | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentCallRecord(SQLModel, table=True):
    """
    Persist a single LLM agent call within a node execution.
    """

    __tablename__ = "agent_calls"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    trace_id: uuid.UUID = Field(index=True)
    node_execution_id: uuid.UUID = Field(index=True)
    agent_name: str
    model_id: str = Field(default="")
    messages_json: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    token_usage_json: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    duration_ms: int | None = None
    started_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    # -- RFC 12.3.6 tool_call extension columns (foundations slice) --
    tool_name: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    capability_token: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    evidence_object_ids: list[str] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    succeeded: bool | None = Field(
        default=None,
        sa_column=Column(Boolean, nullable=True),
    )
    tenant_id: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True, index=True),
    )
