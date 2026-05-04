"""
SQLModel table definition for ``incident_memory`` — long-term per-fund/cluster
incident recall (RFC §4 long-term memory).

One row per resolved investigation worth remembering. Mirrors the relevant
slice of ``investigation_records`` but is **scoped + denormalised** for fast
recall in the SRE pipeline's ``analyse_root_cause`` node:

* ``tenant_id`` + ``cluster_id`` scope every recall query so funds never
  cross-contaminate (RFC §3.1 multi-tenant invariants).
* ``alert_signature`` is the deterministic fingerprint
  ``sha256(sorted(labels) || classification_category)[:16]`` (same convention
  as ``runbook_gap_cluster``) so exact-match lookup ("have we seen this exact
  gap?") is a single B-tree probe.
* ``alert_title`` / ``alert_description`` / ``root_cause`` / ``remediation``
  are denormalised so the analyser prompt can be rendered without a JOIN
  back to ``investigation_records`` — recall is on the hot path of every
  investigation.
* ``occurred_at`` mirrors ``Investigation.completed_at`` and powers the
  recency-window WHERE clause; ``created_at`` is the row insert time
  (debug only).

Companion table ``incident_memory_embeddings`` (one row per memory + section
+ embedder) carries the vector embeddings for similarity recall.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, Float, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field, SQLModel


class IncidentMemoryRecord(SQLModel, table=True):
    """
    One resolved-incident memory row scoped to ``(tenant_id, cluster_id)``.

    Written by the ``publish_findings`` node when an investigation completes
    with confidence ``HIGH`` or ``MEDIUM``. Read by ``analyse_root_cause``
    via :func:`sentinel.domain.memory.embeddings.retrieve_similar_incidents`
    so the analyser prompt sees prior incidents that look like the current
    alert.
    """

    __tablename__ = "incident_memory"
    __table_args__ = (
        Index(
            "ix_incident_memory_tenant_cluster_occurred",
            "tenant_id",
            "cluster_id",
            sa.text("occurred_at DESC"),
        ),
        Index(
            "ix_incident_memory_signature",
            "tenant_id",
            "cluster_id",
            "alert_signature",
        ),
    )

    memory_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    tenant_id: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    cluster_id: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    service: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    alert_signature: str = Field(
        sa_column=Column(sa.String(length=16), nullable=False),
    )
    alert_title: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    alert_description: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    root_cause: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    remediation: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    confidence_score: float = Field(
        sa_column=Column(Float, nullable=False),
    )
    source_investigation_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ("IncidentMemoryRecord",)
