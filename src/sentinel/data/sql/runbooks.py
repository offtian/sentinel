"""
SQLModel table definition for runbook_match (RFC 12.3.2).

Records the runbook chosen for a given alert_request, including the match
method (tag-based, RAG, or generic fallback), confidence score, and the
content-hash version of the runbook bytes that was matched. The
``request_id`` foreign key ties each match back to its envelope row in
``alert_request``.

Schema-only at F3 — no pipeline writers are wired into this table yet.
Writers land in F6 alongside the runbook matcher.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, Float, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field, SQLModel


MatchMethod = Literal["tag", "rag", "generic_fallback"]


class RunbookMatchRecord(SQLModel, table=True):
    """
    Runbook match decision row (RFC 12.3.2).

    The ``request_id`` FK links the match to the canonical alert envelope
    row in ``alert_request``. ``runbook_version_sha`` is the truncated
    sha256 of the runbook bytes; the 32-char width matches the RFC.
    """

    __tablename__ = "runbook_match"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["alert_request.request_id"],
            name="fk_runbook_match_alert_request",
        ),
    )

    match_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    request_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    runbook_id: str = Field(sa_column=Column(Text, nullable=False))
    runbook_version_sha: str = Field(max_length=32)
    match_method: MatchMethod = Field(sa_column=Column(Text, nullable=False))
    match_confidence: float = Field(sa_column=Column(Float, nullable=False))
    matched_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
