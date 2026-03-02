from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSON
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
    findings_json: dict | None = Field(default=None, sa_column=Column(JSON))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class TicketReviewRecord(SQLModel, table=True):
    __tablename__ = "ticket_review_records"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    ticket_id: str = Field(index=True)
    ticket_key: str = Field(index=True)
    suggested_response: str = Field(sa_column=Column(Text))
    sources_json: dict | None = Field(default=None, sa_column=Column(JSON))
    confidence_score: float | None = None
    category: str | None = None
    status: str = Field(default="drafted")
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    reviewed_at: datetime | None = None
