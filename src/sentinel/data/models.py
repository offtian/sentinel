from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class InvestigationRecord(SQLModel, table=True):
    __tablename__ = "investigation_records"

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


class TicketReviewRecord(SQLModel, table=True):
    __tablename__ = "ticket_review_records"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    ticket_id: str = Field(index=True)
    ticket_key: str = Field(index=True)
    suggested_response: str = Field(sa_column=Column(Text))
    sources_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    confidence_score: float | None = None
    category: str | None = None
    status: str = Field(default="drafted")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    reviewed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    trace_id: uuid.UUID | None = Field(default=None, index=True)
