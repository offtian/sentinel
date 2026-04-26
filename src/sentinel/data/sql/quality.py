"""
SQLModel table definitions for quality_verdict + approval_record (RFC 12.3.8).

The quality verdict captures the groundedness assessment for an investigation
(did each claim cite supporting evidence?) plus the confidence score and the
human-readable verdict reason. Each approval row records a downstream
human-in-the-loop decision against a verdict. Schema-only at F3 — pipeline
writers and the approval workflow land in a follow-up slice.

TODO(F3.7): once InvestigationRecord gains a ``request_id`` column, switch the
``quality_verdict.investigation_id`` foreign key to target ``request_id`` to
match the canonical envelope keying used by ``alert_request`` and
``runbook_match`` (RFC 12.3.8 nominally keys these on ``request_id``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field, SQLModel


class QualityVerdictRecord(SQLModel, table=True):
    """
    Quality verdict row (RFC 12.3.8).

    Each row captures a groundedness/confidence assessment for one
    investigation. The ``investigation_id`` FK ties the verdict back to its
    parent ``investigation_records`` row (see TODO at module level for the
    F3.7 follow-up that re-keys this onto ``request_id``).
    """

    __tablename__ = "quality_verdict"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigation_records.id"],
            name="fk_quality_verdict_investigation",
        ),
    )

    verdict_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    investigation_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    groundedness_pass: bool = Field(sa_column=Column(Boolean, nullable=False))
    evidence_ref_count: int = Field(sa_column=Column(Integer, nullable=False))
    confidence_score: float = Field(sa_column=Column(Float, nullable=False))
    verdict_reason: str = Field(sa_column=Column(Text, nullable=False))
    assessed_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ApprovalRecord(SQLModel, table=True):
    """
    Human-in-the-loop approval row (RFC 12.3.8).

    Each row records a single approver's decision against a quality verdict.
    The ``decision`` column is plain Text (no Postgres ENUM at F3, matching
    the F3.4 conventions); the canonical decision vocabulary is enforced at
    the application layer.
    """

    __tablename__ = "approval_record"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["verdict_id"],
            ["quality_verdict.verdict_id"],
            name="fk_approval_record_verdict",
        ),
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    verdict_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    approver: str = Field(sa_column=Column(Text, nullable=False))
    decision: str = Field(sa_column=Column(Text, nullable=False))
    decided_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
