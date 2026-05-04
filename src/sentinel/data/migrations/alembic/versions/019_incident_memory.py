"""
Add incident_memory + incident_memory_embeddings for long-term per-fund/cluster
incident recall.

Revision ID: 019
Revises: 018
Create Date: 2026-05-04

Two-table schema change for the long-term memory recall path:

1. ``incident_memory`` — one row per resolved investigation worth remembering,
   scoped by ``(tenant_id, cluster_id)`` for fast multi-tenant recall plus a
   deterministic ``alert_signature`` for exact-match lookup. Denormalises
   the alert + diagnosis fields so the analyser prompt can render prior
   incidents without joining back to ``investigation_records``.

2. ``incident_memory_embeddings`` — vector index over the ``alert``,
   ``root_cause`` and ``remediation`` sections of every memory row. Mirrors
   the structure of ``runbook_embeddings`` (added in migration 016): same
   ``vector(1536)`` dimension lock, same HNSW + ``vector_cosine_ops`` index,
   same identity tuple shape. The FK to ``incident_memory.memory_id``
   cascades on delete so dropping a memory row drops its embeddings.

The ``pgvector`` extension was created idempotently by migration 016, so
this migration only creates tables + indexes — it does not touch the
extension.

The 1536-d ``embedding`` column is added via raw DDL so this migration does
not need the ``pgvector.sqlalchemy`` Python import at upgrade time.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- incident_memory --
    op.create_table(
        "incident_memory",
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("alert_signature", sa.String(length=16), nullable=False),
        sa.Column("alert_title", sa.Text(), nullable=False),
        sa.Column("alert_description", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("remediation", sa.Text(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column(
            "source_investigation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # B-tree index for the primary recall predicate: scoped by tenant+cluster,
    # ordered by recency so ``LIMIT N`` reads the most recent rows first.
    op.execute(
        "CREATE INDEX ix_incident_memory_tenant_cluster_occurred "
        "ON incident_memory (tenant_id, cluster_id, occurred_at DESC)"
    )
    op.create_index(
        "ix_incident_memory_signature",
        "incident_memory",
        ["tenant_id", "cluster_id", "alert_signature"],
    )

    # -- incident_memory_embeddings --
    op.create_table(
        "incident_memory_embeddings",
        sa.Column(
            "embedding_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("embedding_section", sa.String(length=32), nullable=False),
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
            "embedding_section IN ('alert', 'root_cause', 'remediation')",
            name="ck_incident_memory_embeddings_section",
        ),
        sa.UniqueConstraint(
            "memory_id",
            "embedding_section",
            "embedding_model",
            "embedding_model_ver",
            name="uq_incident_memory_embeddings_identity",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["incident_memory.memory_id"],
            name="fk_incident_memory_embeddings_memory",
            ondelete="CASCADE",
        ),
    )
    op.execute("ALTER TABLE incident_memory_embeddings ADD COLUMN embedding vector(1536) NOT NULL")
    op.create_index(
        "ix_incident_memory_embeddings_memory_id",
        "incident_memory_embeddings",
        ["memory_id"],
    )
    op.create_index(
        "ix_incident_memory_embeddings_model",
        "incident_memory_embeddings",
        ["embedding_model", "embedding_model_ver"],
    )
    # HNSW ANN index for cosine-similarity retrieval. Same parameters as the
    # runbook_embeddings HNSW (m=16, ef_construction=64) — pgvector's
    # recommended starting point for ≤10^4 rows.
    op.execute(
        "CREATE INDEX ix_incident_memory_embeddings_hnsw "
        "ON incident_memory_embeddings "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    # Reverse-create order: drop indexes + embeddings table first (FK child),
    # then incident_memory (FK parent).
    op.execute("DROP INDEX IF EXISTS ix_incident_memory_embeddings_hnsw")
    op.drop_index(
        "ix_incident_memory_embeddings_model",
        table_name="incident_memory_embeddings",
    )
    op.drop_index(
        "ix_incident_memory_embeddings_memory_id",
        table_name="incident_memory_embeddings",
    )
    op.drop_table("incident_memory_embeddings")

    op.drop_index(
        "ix_incident_memory_signature",
        table_name="incident_memory",
    )
    op.execute("DROP INDEX IF EXISTS ix_incident_memory_tenant_cluster_occurred")
    op.drop_table("incident_memory")

    # NOTE: the ``vector`` extension is intentionally NOT dropped here. It
    # was created in migration 016 and is shared with ``runbook_embeddings``;
    # dropping it would fail while that table still uses it, and even when
    # successful would be a database-wide operation. Same convention as
    # 016.downgrade.
