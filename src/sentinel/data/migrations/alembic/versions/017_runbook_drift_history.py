"""
Add F6.L runbook_drift_history table for the daily drift-detection job
(F6 spec §F6.L, RFC §15.10).

Revision ID: 017
Revises: 016
Create Date: 2026-04-26

The drift-detection job (``scripts/runbook_drift_check.py``) writes one
row per detected drift event into ``runbook_drift_history``. Schema is
event-grain — re-detection on a subsequent cron tick inserts a new row
when the detail-hash changes (or when the previous open event is older
than 24h), so MTTR-for-runbook-drift reporting has a per-event
timeline. Resolution is tracked in-place so the dashboard's hot
``WHERE resolved_at IS NULL`` query stays cheap (partial index).

``drift_type`` is an enumerated CHECK constraint with five variants:

- ``fixture_failure``           — a tests.yaml fixture's expected
                                  outcome no longer holds against the
                                  current matcher.
- ``min_tag_score_regression``  — fixture's ``min_tag_score`` floor
                                  dropped below the expected value.
- ``stale_no_matches``          — ``last_validated > 90d`` AND zero
                                  match rows in the last 30d.
- ``tools_yaml_invalid``        — a ``tool_name`` listed in
                                  ``tools.yaml`` is missing from the
                                  project's allowed-tool registry.
- ``content_sha_mismatch``      — frontmatter ``content_sha`` differs
                                  from the loader-computed sha (CI
                                  integrity violation; usually means
                                  the pre-commit hook didn't run).

``drift_severity`` is an enumerated CHECK constraint (``low``,
``medium``, ``high``) so dashboard routing rules can fan-out paging
based on severity without parsing ``drift_detail``.

``drift_detail`` is JSONB; the per-``drift_type`` payload schema is
declared and validated app-side via a Pydantic discriminated union
in :mod:`sentinel.data.sql.runbook_drift`. JSONB (rather than per-
type narrow columns) keeps the schema stable when new drift variants
are added — a bare ``ALTER TABLE`` then suffices instead of a fresh
migration per field.

Indexes:

- ``ix_runbook_drift_history_runbook_id`` — per-runbook drift
  digests for the runbook-owner weekly summary.
- ``ix_runbook_drift_history_detected_at`` — DESC on detection
  timestamp for the "what fired today?" dashboard query.
- ``ix_runbook_drift_history_unresolved`` — partial index
  ``WHERE resolved_at IS NULL`` for the hot dashboard query;
  keeps the index narrow as the resolved-table grows.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- F6.L.1: runbook_drift_history table --------------------------------
    # Event-grain (one row per detection); resolution is patched in-place via
    # the ``resolved_at`` / ``resolved_by`` / ``resolution_pr_url`` columns.
    op.create_table(
        "runbook_drift_history",
        sa.Column(
            "drift_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("runbook_id", sa.String(length=255), nullable=False),
        sa.Column("runbook_content_sha", sa.String(length=32), nullable=False),
        sa.Column("drift_type", sa.String(length=64), nullable=False),
        sa.Column("drift_severity", sa.String(length=16), nullable=False),
        sa.Column("drift_detail", postgresql.JSONB(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("detected_by", sa.String(length=64), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolution_pr_url", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "drift_type IN ("
            "'fixture_failure', "
            "'min_tag_score_regression', "
            "'stale_no_matches', "
            "'tools_yaml_invalid', "
            "'content_sha_mismatch'"
            ")",
            name="ck_runbook_drift_history_drift_type",
        ),
        sa.CheckConstraint(
            "drift_severity IN ('low', 'medium', 'high')",
            name="ck_runbook_drift_history_drift_severity",
        ),
    )

    # -- F6.L.1: standard non-partial indexes -------------------------------
    op.create_index(
        "ix_runbook_drift_history_runbook_id",
        "runbook_drift_history",
        ["runbook_id"],
    )
    op.create_index(
        "ix_runbook_drift_history_detected_at",
        "runbook_drift_history",
        [sa.text("detected_at DESC")],
    )

    # -- F6.L.1: partial index for the hot "open drift" dashboard query ----
    # Dashboard pulls open drift hundreds of times a day; the resolved-table
    # grows linearly with detected events. Partial WHERE keeps the index
    # narrow so its size tracks open-event count, not all-time event count.
    op.create_index(
        "ix_runbook_drift_history_unresolved",
        "runbook_drift_history",
        ["runbook_id", "drift_type"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    # -- F6.L.1 reverse: drop indexes (partial first, then standard) -------
    op.drop_index(
        "ix_runbook_drift_history_unresolved",
        table_name="runbook_drift_history",
    )
    op.drop_index(
        "ix_runbook_drift_history_detected_at",
        table_name="runbook_drift_history",
    )
    op.drop_index(
        "ix_runbook_drift_history_runbook_id",
        table_name="runbook_drift_history",
    )
    op.drop_table("runbook_drift_history")
