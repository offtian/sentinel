"""
SQLModel table definitions for investigation_task + task_status_change (RFC 12.3.7).

The investigation task list breaks an investigation down into individually
trackable steps; the status-change row provides the audit trail of how each
task moved through its lifecycle. Schema-only at F3 — pipeline writers land
in a follow-up slice.

TODO(F3.7): once InvestigationRecord gains a ``request_id`` column, switch the
``investigation_task.investigation_id`` foreign key to target ``request_id``
to match the canonical envelope keying used by ``alert_request`` and
``runbook_match`` (RFC 12.3.7 nominally keys these on ``request_id``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field, SQLModel


class InvestigationTaskRecord(SQLModel, table=True):
    """
    Investigation task list row (RFC 12.3.7).

    Each row represents a single trackable step within an investigation.
    The ``investigation_id`` FK ties the task back to its parent
    ``investigation_records`` row (see TODO at module level for the F3.7
    follow-up that re-keys this onto ``request_id``).
    """

    __tablename__ = "investigation_task"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigation_records.id"],
            name="fk_investigation_task_investigation",
        ),
    )

    task_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    investigation_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    task_text: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    evidence_refs: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )


class TaskStatusChangeRecord(SQLModel, table=True):
    """
    Task status-change audit row (RFC 12.3.7).

    Captures each lifecycle transition for a row in ``investigation_task``.
    The first transition for a task has ``from_status = NULL`` (no prior
    status); subsequent rows record the previous status, the new status,
    a tz-aware timestamp, and an optional reason.
    """

    __tablename__ = "task_status_change"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["investigation_task.task_id"],
            name="fk_task_status_change_task",
        ),
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    task_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    from_status: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    to_status: str = Field(sa_column=Column(Text, nullable=False))
    at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    reason: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
