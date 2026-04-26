"""
Add F4.7 ReplayBundle persistence columns to pipeline_runs (RFC §3.8).

Revision ID: 014
Revises: 013
Create Date: 2026-04-26

Adds two nullable columns so each pipeline_runs row can carry the
canonical RFC §3.8 ReplayBundle written by the F4.7 tracer:

- replay_bundle_json   JSONB    nullable    Full canonical bundle (envelope,
                                            alert payload, tool I/O, LLM I/O,
                                            runbook ids, final outputs).
- replay_bundle_sha    text     nullable    SHA-256 over the canonical JSON,
                                            indexed for lookup by digest and to
                                            surface canonicalisation drift.

Both columns are nullable for rolling-deploy safety: pre-F4.7 rows simply
carry NULL until they are re-run.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- F4.7: pipeline_runs replay-bundle columns (RFC §3.8) --
    op.add_column(
        "pipeline_runs",
        sa.Column("replay_bundle_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("replay_bundle_sha", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_pipeline_runs_replay_bundle_sha",
        "pipeline_runs",
        ["replay_bundle_sha"],
    )


def downgrade() -> None:
    # -- F4.7 reverse: drop index then columns (reverse-add order) --
    op.drop_index(
        "ix_pipeline_runs_replay_bundle_sha",
        table_name="pipeline_runs",
    )
    op.drop_column("pipeline_runs", "replay_bundle_sha")
    op.drop_column("pipeline_runs", "replay_bundle_json")
