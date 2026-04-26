from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field, SQLModel


class AuditLogRecord(SQLModel, table=True):
    """
    Append-only audit trail for regulatory traceability.

    The database role used by the application should have INSERT-only permissions
    on this table. No UPDATE or DELETE should ever be performed. The
    ``audit_log_worm_guard`` Postgres trigger (RFC 12.3.10) enforces this at the
    storage layer in addition to any role-level grants.

    The ``row_hash`` column is populated server-side by the
    ``audit_log_compute_row_hash`` BEFORE INSERT trigger; Python writers do not
    set it. ``prev_hash`` carries the previous row's ``row_hash`` in the same
    ``request_id`` chain — the first row in a chain has no predecessor.
    """

    __tablename__ = "audit_log"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    actor: str
    action: str = Field(index=True)
    resource_type: str = Field(index=True)
    resource_id: str = Field(index=True)
    details_json: str = Field(sa_column=Column(Text, nullable=False))
    input_hash: str = Field(max_length=64)
    model_id: str = Field(default="")
    prompt_version: str = Field(default="")
    prompt_sha256: str | None = Field(default=None, max_length=64)
    pipeline_run_id: uuid.UUID | None = Field(default=None, index=True)
    request_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(UUID(as_uuid=True), nullable=True, index=True),
    )
    prev_hash: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    row_hash: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
