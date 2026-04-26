from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Boolean, Column, DateTime, ForeignKeyConstraint, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field, SQLModel


class InvestigationRecord(SQLModel, table=True):
    __tablename__ = "investigation_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id"],
            ["alert_request.request_id"],
            name="fk_investigation_alert_request",
        ),
        ForeignKeyConstraint(
            ["runbook_match_id"],
            ["runbook_match.match_id"],
            name="fk_investigation_runbook_match",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    alert_source: str
    alert_id: str = Field(index=True)
    alert_title: str
    severity: str
    service: str
    status: str = Field(default="pending")
    root_cause: str | None = Field(default=None, sa_column=Column(Text))
    remediation: str | None = Field(default=None, sa_column=Column(Text))
    confidence_score: float | None = None
    findings_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    trace_id: uuid.UUID | None = Field(default=None, index=True)

    # -- RFC 12.3.4 investigation extension columns (foundations slice) --
    request_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(UUID(as_uuid=True), nullable=True, index=True),
    )
    runbook_match_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(UUID(as_uuid=True), nullable=True),
    )
    model_id_primary: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    iteration_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    terminated_reason: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    loop_cap_hit: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=sa.text("false")),
    )
