from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class JobRequestRecord(SQLModel, table=True):
    """
    Persistent representation of a queued job.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` in the worker poll loop
    to implement a PostgreSQL-backed work queue without external dependencies.
    """

    __tablename__ = "job_requests"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_job_requests_idempotency_key"),
        Index("ix_job_requests_status_priority", "status", "priority", "created_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_type: str  # "investigation" | "support_review"
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    payload_hash: str = Field(max_length=64)
    status: str = Field(default="pending", index=True)
    priority: int = Field(default=1)
    requested_by: str = Field(default="")
    idempotency_key: str = Field(max_length=64)
    locked_by: str | None = Field(default=None)
    locked_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    trace_id: uuid.UUID | None = Field(default=None, index=True)


class JobResultRecord(SQLModel, table=True):
    """Persistent representation of a job execution outcome."""

    __tablename__ = "job_results"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_request_id: uuid.UUID = Field(index=True)
    status: str  # "completed" | "failed" | "timed_out"
    result_json: str | None = Field(default=None, sa_column=Column(Text))
    error_message: str | None = Field(default=None, sa_column=Column(Text))
    started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    duration_ms: int | None = None
    worker_id: str = Field(default="")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
