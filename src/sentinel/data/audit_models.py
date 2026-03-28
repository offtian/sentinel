from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel


class AuditLogRecord(SQLModel, table=True):
    """
    Append-only audit trail for regulatory traceability.

    The database role used by the application should have INSERT-only permissions
    on this table. No UPDATE or DELETE should ever be performed.
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
