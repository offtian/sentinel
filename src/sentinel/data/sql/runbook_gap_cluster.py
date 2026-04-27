"""
SQLModel table definition for runbook_gap_cluster (F6 spec §F6.M.2).

The closed-loop runbook-gap detector clusters recent ``runbook_match`` rows
where ``match_method = 'no_match'`` by deterministic fingerprint
(``sha256(sorted_alert_labels || classification_category)[:16]``) so identical
gaps collapse to one row. Each row carries a denormalised ``member_count``,
the most recent representative summary, and — once the weekly job opens a
draft PR — the PR URL and lifecycle dispositions used by operators to grade
flywheel quality.

A cluster is **idempotent on fingerprint**: re-running the weekly job for the
same fingerprint increments ``member_count`` and ``flywheel_iteration``,
merges new ``request_id``s into the capped JSONB array, and refreshes
``last_seen_at`` / ``distinct_services`` / ``distinct_alertnames``. The
fingerprint UNIQUE constraint enforces dedup at the DB level so two parallel
runs cannot create duplicate cluster rows.

The Python-side ``DraftPrDisposition`` Literal mirrors the Postgres CHECK
constraint declared in migration 018 — callsite typos surface at write time
through the SQLModel type rather than only at INSERT.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field, SQLModel


DraftPrDisposition = Literal[
    "merged",
    "closed_no_action",
    "duplicate_of_existing",
    "in_review",
    "rejected_low_signal",
]


class RunbookGapClusterRecord(SQLModel, table=True):
    """
    Closed-loop runbook-gap cluster row (F6 spec §F6.M.2).

    Upserted on ``fingerprint`` by ``scripts/runbook_gap_flywheel.py``.
    ``member_count`` is denormalised so the threshold check reads one column
    instead of scanning the JSONB array. ``flywheel_iteration`` increments on
    every weekly re-detection — a high iteration count with
    ``disposition='closed_no_action'`` is the chronicity signal for ops.
    """

    __tablename__ = "runbook_gap_cluster"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_runbook_gap_cluster_fingerprint"),
        Index("ix_runbook_gap_cluster_last_seen", sa.text("last_seen_at DESC")),
    )

    cluster_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    fingerprint: str = Field(
        sa_column=Column(sa.String(length=16), nullable=False),
    )
    classification_category: str = Field(
        sa_column=Column(sa.String(length=255), nullable=False),
    )
    representative_alert_summary: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    member_request_ids: list[str] = Field(
        sa_column=Column(JSONB, nullable=False),
    )
    member_count: int = Field(
        sa_column=Column(sa.Integer, nullable=False),
    )
    distinct_services: list[str] = Field(
        sa_column=Column(JSONB, nullable=False),
    )
    distinct_alertnames: list[str] = Field(
        sa_column=Column(JSONB, nullable=False),
    )
    first_seen_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_seen_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    draft_pr_url: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    draft_pr_opened_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    draft_pr_closed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    draft_pr_disposition: DraftPrDisposition | None = Field(
        default=None,
        sa_column=Column(sa.String(length=32), nullable=True),
    )
    flywheel_iteration: int = Field(
        default=1,
        sa_column=Column(sa.Integer, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


# Re-export the JSONB-typed list signature so callers can hint cleanly without
# pulling :class:`Any` into their own modules. The application keeps the JSONB
# arrays as plain ``list[str]``; the type alias documents that contract.
JsonbStringArray = list[str]
"""Type alias for the JSONB-backed string arrays on this row."""

__all__ = (
    "DraftPrDisposition",
    "JsonbStringArray",
    "RunbookGapClusterRecord",
)


# Help mypy treat the imports above as used when this module is consumed
# only for its table registration (e.g. in the alembic env.py side-effect
# import). The local re-export tuple suffices.
_ = (Any,)
