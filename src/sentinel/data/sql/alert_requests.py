"""
SQLModel table definition for alert_request (RFC 12.3.1).

The canonical row written by the ingestion / dedup stage of the SRE pipeline.
Each row is keyed by ``request_id`` (the envelope's identifier) and carries the
redacted alert payload plus dedup metadata.

Schema-only at F3 — no pipeline writers are wired into this table yet. Writers
land in F4 alongside envelope propagation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field, SQLModel


AlertProvider = Literal["pagerduty", "datadog", "alertmanager"]
DedupStatus = Literal["new", "duplicate"]


class AlertRequestRecord(SQLModel, table=True):
    """
    Canonical alert ingestion row (RFC 12.3.1).

    The ``request_id`` is the envelope identifier propagated to spans, the
    runbook_match table, and downstream investigation rows.
    """

    __tablename__ = "alert_request"
    __table_args__ = (
        Index(
            "ix_alert_request_tenant_received",
            "tenant_id",
            sa.text("received_at DESC"),
        ),
        Index(
            "ix_alert_request_provider_alert_id",
            "provider",
            "alert_id",
        ),
    )

    request_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    tenant_id: str = Field(index=True)
    received_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    provider: AlertProvider = Field(sa_column=Column(Text, nullable=False))
    alert_id: str
    severity: str
    redacted_annotations: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    dedup_status: DedupStatus = Field(sa_column=Column(Text, nullable=False))
