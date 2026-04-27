"""
Add F6.M runbook_gap_cluster table for the weekly fingerprint-clustering
auto-PR flywheel (RFC §3.3 + F6 spec §F6.M).

Revision ID: 018
Revises: 017
Create Date: 2026-04-26

Schema for the closed-loop runbook-gap detector. The weekly job
``scripts/runbook_gap_flywheel.py`` walks recent ``runbook_match`` rows where
``match_method = 'no_match'``, fingerprints each row by
``sha256(sorted_alert_labels || classification_category)[:16]`` so identical
gaps collapse to one row, and upserts into this table. Clusters with
``member_count >= flywheel_min_cluster_size`` then have a draft PR opened
against the catalog with a templated ``RUNBOOK.md`` skeleton routed to the
team owner via CODEOWNERS.

Schema rationale (F6 spec §F6.M):

* ``fingerprint UNIQUE`` — the upsert key. Weekly job never duplicates
  clusters or PRs because the constraint deduplicates at the DB level.
* ``member_count`` denormalised — the threshold check (``>= 3`` by default)
  reads this column instead of scanning the JSONB array on every weekly run.
* ``member_request_ids`` capped at last-100 by the upsert callsite —
  query-friendly, bounded growth. Full history lives in ``runbook_match``
  joinable by ``request_id``.
* ``draft_pr_disposition`` — closed-loop measurement: are auto-PRs noise or
  signal? Operator updates manually post-merge in v1; future API.
* ``flywheel_iteration`` — chronicity signal: a cluster that re-fires at
  iteration 5 with ``disposition='closed_no_action'`` from iteration 1
  deserves different routing than a brand-new gap.
* Partial index on ``draft_pr_url WHERE draft_pr_closed_at IS NULL`` —
  "show me open auto-PRs" stays cheap as the resolved PRs accumulate.

The ``draft_pr_disposition`` CHECK constraint enforces the disposition
vocabulary at the DB level; the Python-side ``RunbookGapClusterRecord`` mirrors
it via a SQLModel ``Literal`` so callsite typos surface at write time.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


_DISPOSITION_VOCAB = (
    "merged",
    "closed_no_action",
    "duplicate_of_existing",
    "in_review",
    "rejected_low_signal",
)


def upgrade() -> None:
    # -- F6.M.1: runbook_gap_cluster table --
    op.create_table(
        "runbook_gap_cluster",
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("fingerprint", sa.String(length=16), nullable=False),
        sa.Column("classification_category", sa.String(length=255), nullable=False),
        sa.Column("representative_alert_summary", sa.Text(), nullable=False),
        sa.Column("member_request_ids", postgresql.JSONB(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("distinct_services", postgresql.JSONB(), nullable=False),
        sa.Column("distinct_alertnames", postgresql.JSONB(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("draft_pr_url", sa.Text(), nullable=True),
        sa.Column("draft_pr_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft_pr_closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft_pr_disposition", sa.String(length=32), nullable=True),
        sa.Column(
            "flywheel_iteration",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("fingerprint", name="uq_runbook_gap_cluster_fingerprint"),
        sa.CheckConstraint(
            "draft_pr_disposition IN ("
            + ", ".join(f"'{value}'" for value in _DISPOSITION_VOCAB)
            + ")",
            name="ck_runbook_gap_cluster_disposition",
        ),
    )

    # Hot read: the dashboard "show me what surfaced this week" timeline.
    op.create_index(
        "ix_runbook_gap_cluster_last_seen",
        "runbook_gap_cluster",
        [sa.text("last_seen_at DESC")],
    )

    # Hot read: "show me every open auto-PR". Partial index keeps the index
    # tight even as historical clusters accumulate with closed PRs — the
    # weekly re-run consults this set to skip clusters whose PR is still
    # open against authors.
    op.create_index(
        "ix_runbook_gap_cluster_open_prs",
        "runbook_gap_cluster",
        ["draft_pr_url"],
        postgresql_where=sa.text("draft_pr_closed_at IS NULL AND draft_pr_url IS NOT NULL"),
    )


def downgrade() -> None:
    # Drop indexes before dropping the table so a partially-failed downgrade
    # leaves no dangling index objects.
    op.drop_index("ix_runbook_gap_cluster_open_prs", table_name="runbook_gap_cluster")
    op.drop_index("ix_runbook_gap_cluster_last_seen", table_name="runbook_gap_cluster")
    op.drop_table("runbook_gap_cluster")
