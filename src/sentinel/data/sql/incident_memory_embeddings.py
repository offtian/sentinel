"""
SQLModel table definition for ``incident_memory_embeddings`` — vector index
backing the long-term memory recall path.

Direct structural copy of :mod:`sentinel.data.sql.runbook_embeddings` —
same dimension lock (``vector(1536)``), same HNSW + cosine index strategy,
same identity tuple shape ``(memory_id, embedding_section, embedding_model,
embedding_model_ver)`` so re-indexing the same row with the same embedder
is a no-op.

Three sections per memory: ``alert`` (title + description),
``root_cause``, ``remediation`` — embedded separately so a similarity
query can match on whichever angle is closest to the new incident's
prompt text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field, SQLModel


IncidentEmbeddingSection = Literal["alert", "root_cause", "remediation"]


# Same 1536-d dimension lock as the runbook embedder — both share the same
# embedder configuration knob (``Settings.runbook_embedder_llm``).
INCIDENT_MEMORY_EMBEDDING_DIM: int = 1536


class IncidentMemoryEmbeddingRecord(SQLModel, table=True):
    """
    One vector row per ``(memory_id, embedding_section, embedding_model,
    embedding_model_ver)`` for incident-memory similarity recall.

    Mirrors :class:`sentinel.data.sql.runbook_embeddings.RunbookEmbeddingRecord`
    so the F6.J retrieval primitives transfer directly. The FK to
    ``incident_memory.memory_id`` cascades on delete: dropping a memory row
    drops its embeddings with it.
    """

    __tablename__ = "incident_memory_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["memory_id"],
            ["incident_memory.memory_id"],
            name="fk_incident_memory_embeddings_memory",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "memory_id",
            "embedding_section",
            "embedding_model",
            "embedding_model_ver",
            name="uq_incident_memory_embeddings_identity",
        ),
        CheckConstraint(
            "embedding_section IN ('alert', 'root_cause', 'remediation')",
            name="ck_incident_memory_embeddings_section",
        ),
        Index("ix_incident_memory_embeddings_memory_id", "memory_id"),
        Index(
            "ix_incident_memory_embeddings_model",
            "embedding_model",
            "embedding_model_ver",
        ),
    )

    embedding_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    memory_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    embedding_section: IncidentEmbeddingSection = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    embedding_model: str = Field(
        sa_column=Column(sa.String(length=255), nullable=False),
    )
    embedding_model_ver: str = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    embedding_dim: int = Field(
        sa_column=Column(sa.Integer, nullable=False),
    )
    embedding: list[float] = Field(
        sa_column=Column(Vector(INCIDENT_MEMORY_EMBEDDING_DIM), nullable=False),
    )
    source_text: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    source_text_sha: str = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    indexed_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = (
    "INCIDENT_MEMORY_EMBEDDING_DIM",
    "IncidentEmbeddingSection",
    "IncidentMemoryEmbeddingRecord",
)
