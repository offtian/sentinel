"""
Add F6.J pgvector extension + runbook_embeddings + runbook_rag_match_evidence
(RFC §4.2 RAG fallback / F6 spec §F6.J).

Revision ID: 016
Revises: 015
Create Date: 2026-04-26

Three-part schema change for the F6 runbook catalog Stage 3 RAG fallback:

1. Enable the ``pgvector`` extension. ``CREATE EXTENSION IF NOT EXISTS`` so the
   migration is idempotent on environments where the extension is already
   present (production, shared test DB).

2. ``runbook_embeddings`` — one row per (runbook_id, content_sha,
   embedding_section, embedding_model, embedding_model_ver). Stores the
   1536-dimensional embedding vector emitted by the in-process LiteLLM
   embedder for each of the three embedded sections (description / body /
   applies_to). The HNSW index over ``embedding`` powers Stage 3
   nearest-neighbour retrieval.

   **Dimension lock.** v1 pins ``vector(1536)`` because pgvector requires a
   fixed dimension at index creation, and 1536 is the native dimension of
   OpenAI ``text-embedding-3-small`` plus the larger Ollama embedders we
   currently support. Multi-dimensional support (a per-row dimension column
   plus partial indexes) is deferred to ``runbook-rag-multidim.md``.

3. ``runbook_rag_match_evidence`` — top-k candidate audit rows written by the
   Stage 3 matcher path. One row per (match_id, candidate, rank), so replay
   can reconstruct the original similarity ranking without re-querying the
   live HNSW index (which can be rebuilt or have its model swapped).

   Plus three nullable columns on ``runbook_match`` (``rag_query_source_sha``,
   ``rag_top_k``, ``rag_min_similarity``) capturing the Stage 3 query
   parameters for replay determinism.

All new columns on ``runbook_match`` are nullable for rolling-deploy safety:
pre-F6.J rows simply carry NULL until they are re-matched through the Stage 3
path.

The 1536-d embedding column is declared via raw DDL so the migration does
not require importing ``pgvector.sqlalchemy.Vector`` at upgrade time
(keeps the migration runnable even before the package is fully installed
in a clean environment, and keeps the file readable as a pure schema
contract). The SQLModel definition in ``data/sql/runbook_embeddings.py``
imports ``Vector`` for the application-side row binding.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- F6.J.1: pgvector extension (idempotent) --
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # -- F6.J.1: runbook_embeddings table --
    # The ``embedding`` vector(1536) column is added via raw DDL because
    # SQLAlchemy does not ship a first-class type for pgvector vectors and
    # we keep the migration free of the pgvector Python import.
    op.create_table(
        "runbook_embeddings",
        sa.Column(
            "embedding_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("runbook_id", sa.String(length=255), nullable=False),
        sa.Column("content_sha", sa.String(length=32), nullable=False),
        sa.Column("embedding_section", sa.String(length=64), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("embedding_model_ver", sa.String(length=32), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_text_sha", sa.String(length=32), nullable=False),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "embedding_section IN ('description', 'body', 'applies_to')",
            name="ck_runbook_embeddings_embedding_section",
        ),
        sa.UniqueConstraint(
            "runbook_id",
            "content_sha",
            "embedding_section",
            "embedding_model",
            "embedding_model_ver",
            name="uq_runbook_embeddings_identity",
        ),
    )
    # Add the pgvector column via raw DDL.
    op.execute("ALTER TABLE runbook_embeddings ADD COLUMN embedding vector(1536) NOT NULL")
    # B-tree indexes for filtered scans and per-runbook lookups.
    op.create_index(
        "ix_runbook_embeddings_runbook_id",
        "runbook_embeddings",
        ["runbook_id"],
    )
    op.create_index(
        "ix_runbook_embeddings_content_sha",
        "runbook_embeddings",
        ["content_sha"],
    )
    op.create_index(
        "ix_runbook_embeddings_model",
        "runbook_embeddings",
        ["embedding_model", "embedding_model_ver"],
    )
    # HNSW ANN index for cosine-similarity retrieval. m=16 / ef_construction=64
    # is the pgvector recommended starting point for ≤10^4 rows.
    op.execute(
        "CREATE INDEX ix_runbook_embeddings_hnsw ON runbook_embeddings "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # -- F6.J.1: runbook_rag_match_evidence table --
    op.create_table(
        "runbook_rag_match_evidence",
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("candidate_runbook_id", sa.String(length=255), nullable=False),
        sa.Column("candidate_content_sha", sa.String(length=32), nullable=False),
        sa.Column("embedding_section", sa.String(length=64), nullable=False),
        sa.Column("cosine_similarity", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("embedding_model_ver", sa.String(length=32), nullable=False),
        sa.Column(
            "queried_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["runbook_match.match_id"],
            name="fk_runbook_rag_match_evidence_match",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_runbook_rag_match_evidence_match_id",
        "runbook_rag_match_evidence",
        ["match_id"],
    )
    op.create_index(
        "ix_runbook_rag_match_evidence_candidate",
        "runbook_rag_match_evidence",
        ["candidate_runbook_id", "candidate_content_sha"],
    )

    # -- F6.J.1: runbook_match Stage 3 audit columns --
    op.add_column(
        "runbook_match",
        sa.Column("rag_query_source_sha", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "runbook_match",
        sa.Column("rag_top_k", sa.Integer(), nullable=True),
    )
    op.add_column(
        "runbook_match",
        sa.Column("rag_min_similarity", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    # -- F6.J.1 reverse: drop runbook_match Stage 3 columns (reverse-add order) --
    op.drop_column("runbook_match", "rag_min_similarity")
    op.drop_column("runbook_match", "rag_top_k")
    op.drop_column("runbook_match", "rag_query_source_sha")

    # -- F6.J.1 reverse: drop runbook_rag_match_evidence indexes then table --
    op.drop_index(
        "ix_runbook_rag_match_evidence_candidate",
        table_name="runbook_rag_match_evidence",
    )
    op.drop_index(
        "ix_runbook_rag_match_evidence_match_id",
        table_name="runbook_rag_match_evidence",
    )
    op.drop_table("runbook_rag_match_evidence")

    # -- F6.J.1 reverse: drop runbook_embeddings indexes then table --
    # Dropping the table also drops the HNSW index, but we drop named
    # indexes explicitly first for symmetry with the upgrade order so a
    # partial-rollback survivor leaves no orphaned objects.
    op.execute("DROP INDEX IF EXISTS ix_runbook_embeddings_hnsw")
    op.drop_index(
        "ix_runbook_embeddings_model",
        table_name="runbook_embeddings",
    )
    op.drop_index(
        "ix_runbook_embeddings_content_sha",
        table_name="runbook_embeddings",
    )
    op.drop_index(
        "ix_runbook_embeddings_runbook_id",
        table_name="runbook_embeddings",
    )
    op.drop_table("runbook_embeddings")

    # NOTE: the ``vector`` extension is intentionally NOT dropped here.
    # ``DROP EXTENSION vector`` would fail if any surviving table holds a
    # vector column, and even when it succeeds it is a database-wide
    # operation that affects every schema sharing the cluster. Operators
    # who genuinely want to remove pgvector should issue
    # ``DROP EXTENSION vector CASCADE`` manually after confirming no
    # other Sentinel deployment depends on it.
