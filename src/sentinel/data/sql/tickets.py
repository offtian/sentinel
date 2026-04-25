from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


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
