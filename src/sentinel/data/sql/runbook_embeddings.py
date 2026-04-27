"""
SQLModel table definitions for the F6.J runbook RAG fallback (RFC §4.2).

Two tables back the Stage 3 nearest-neighbour retrieval path:

``RunbookEmbeddingRecord`` is one row per
``(runbook_id, content_sha, embedding_section, embedding_model,
embedding_model_ver)``. It stores the 1536-d embedding emitted by the
in-process LiteLLM embedder over each of three runbook sections (description
/ body / applies_to). The unique constraint plus the upsert path in
``domain.runbooks.rag.index_runbook`` make re-indexing on the same
``content_sha`` a no-op, so the F6.K change-watch hook can safely re-walk
the whole catalog on every reload without bloating the table.

``RunbookRagMatchEvidenceRecord`` is one row per Stage 3 candidate per
match. The matcher writes the top-k similarity rows so replay can
reconstruct the original ranking from the audit trail rather than the
live HNSW index (which can be rebuilt or have its model swapped without
breaking determinism).

The 1536-d ``embedding`` column is mapped via :class:`pgvector.sqlalchemy.Vector`
so SQLAlchemy hands the column off to asyncpg as a list of floats and
materialises pgvector ``vector(...)`` values back into Python lists on
read. The column dimension is locked at 1536 in v1 — see the migration
docstring for the multi-dim deferral.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Column, DateTime, Float, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field, SQLModel


EmbeddingSection = Literal["description", "body", "applies_to"]


# v1 dimension lock. Multi-dim support deferred to runbook-rag-multidim.md.
RUNBOOK_EMBEDDING_DIM: int = 1536


class RunbookEmbeddingRecord(SQLModel, table=True):
    """
    Per-section runbook embedding row backing the F6.J Stage 3 RAG retrieval.

    Identity is the composite ``(runbook_id, content_sha, embedding_section,
    embedding_model, embedding_model_ver)`` so every (runbook version,
    section, embedder, embedder version) combination has at most one row.
    Bumping the runbook ``content_sha`` (any byte-level change in the
    quartet) writes new rows; old rows are kept until the operator runs the
    pruning job in a follow-up plan.

    The ``source_text`` column captures the exact bytes fed to the embedder
    (debug + replay), and ``source_text_sha`` is its sha256[:32] so callers
    can detect intent-level drift cheaply without re-reading the body.
    """

    __tablename__ = "runbook_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "runbook_id",
            "content_sha",
            "embedding_section",
            "embedding_model",
            "embedding_model_ver",
            name="uq_runbook_embeddings_identity",
        ),
        CheckConstraint(
            "embedding_section IN ('description', 'body', 'applies_to')",
            name="ck_runbook_embeddings_embedding_section",
        ),
        Index("ix_runbook_embeddings_runbook_id", "runbook_id"),
        Index("ix_runbook_embeddings_content_sha", "content_sha"),
        Index(
            "ix_runbook_embeddings_model",
            "embedding_model",
            "embedding_model_ver",
        ),
    )

    embedding_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    runbook_id: str = Field(
        sa_column=Column(sa.String(length=255), nullable=False),
    )
    content_sha: str = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    embedding_section: EmbeddingSection = Field(
        sa_column=Column(sa.String(length=64), nullable=False),
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
        sa_column=Column(Vector(RUNBOOK_EMBEDDING_DIM), nullable=False),
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


class RunbookRagMatchEvidenceRecord(SQLModel, table=True):
    """
    Top-k Stage 3 candidate audit row for a runbook match (F6.J §J.5).

    One row per candidate per match — when the matcher's Stage 3 path runs,
    it persists the top-k retrieval result so replay can reconstruct the
    original similarity ranking without re-querying the live pgvector
    index. ``cosine_similarity`` is in ``[-1, 1]`` (1.0 = identical, 0.0
    = orthogonal) so consumers can compare across embedders without
    re-applying the ``1 - distance`` arithmetic.

    The ``match_id`` foreign key cascades on delete: dropping a
    ``runbook_match`` row drops its evidence trail with it, keeping the
    audit table aligned with the canonical match table.
    """

    __tablename__ = "runbook_rag_match_evidence"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["runbook_match.match_id"],
            name="fk_runbook_rag_match_evidence_match",
            ondelete="CASCADE",
        ),
        Index("ix_runbook_rag_match_evidence_match_id", "match_id"),
        Index(
            "ix_runbook_rag_match_evidence_candidate",
            "candidate_runbook_id",
            "candidate_content_sha",
        ),
    )

    evidence_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    match_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    candidate_runbook_id: str = Field(
        sa_column=Column(sa.String(length=255), nullable=False),
    )
    candidate_content_sha: str = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    embedding_section: EmbeddingSection = Field(
        sa_column=Column(sa.String(length=64), nullable=False),
    )
    cosine_similarity: float = Field(
        sa_column=Column(Float, nullable=False),
    )
    rank: int = Field(
        sa_column=Column(sa.Integer, nullable=False),
    )
    embedding_model: str = Field(
        sa_column=Column(sa.String(length=255), nullable=False),
    )
    embedding_model_ver: str = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    queried_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
