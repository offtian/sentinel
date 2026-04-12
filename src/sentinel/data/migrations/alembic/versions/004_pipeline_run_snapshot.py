"""
Add replay snapshot columns to pipeline_runs and audit_log.

Revision ID: 004
Revises: 003
Create Date: 2026-04-12

Adds to pipeline_runs:
- input_hash, model_ids_json, mcp_endpoints_json, skill_activations_json
- final_reply, prompt_version, prompt_sha256, prompt_text

Adds to audit_log (idempotent for slice-4 overlap):
- prompt_sha256, pipeline_run_id
"""

import sqlalchemy as sa
from alembic import op


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """
    Check whether a column already exists on the given table.

    Uses the Alembic connection to introspect via SQLAlchemy inspector.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    # -- pipeline_runs: replay snapshot columns --
    op.add_column(
        "pipeline_runs",
        sa.Column("input_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("model_ids_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("mcp_endpoints_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("skill_activations_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("final_reply", sa.JSON(), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("prompt_version", sa.String(128), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("prompt_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("prompt_text", sa.Text(), nullable=True),
    )
    op.create_index("ix_pipeline_runs_input_hash", "pipeline_runs", ["input_hash"])
    op.create_index("ix_pipeline_runs_prompt_sha256", "pipeline_runs", ["prompt_sha256"])

    # -- audit_log: prompt traceability columns (idempotent) --
    if not _column_exists("audit_log", "prompt_sha256"):
        op.add_column(
            "audit_log",
            sa.Column("prompt_sha256", sa.String(64), nullable=True),
        )
    if not _column_exists("audit_log", "pipeline_run_id"):
        op.add_column(
            "audit_log",
            sa.Column("pipeline_run_id", sa.Uuid(), nullable=True, index=True),
        )


def downgrade() -> None:
    # -- audit_log --
    if _column_exists("audit_log", "pipeline_run_id"):
        op.drop_column("audit_log", "pipeline_run_id")
    if _column_exists("audit_log", "prompt_sha256"):
        op.drop_column("audit_log", "prompt_sha256")

    # -- pipeline_runs --
    op.drop_index("ix_pipeline_runs_input_hash", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_prompt_sha256", table_name="pipeline_runs")
    op.drop_column("pipeline_runs", "prompt_text")
    op.drop_column("pipeline_runs", "prompt_sha256")
    op.drop_column("pipeline_runs", "prompt_version")
    op.drop_column("pipeline_runs", "final_reply")
    op.drop_column("pipeline_runs", "skill_activations_json")
    op.drop_column("pipeline_runs", "mcp_endpoints_json")
    op.drop_column("pipeline_runs", "model_ids_json")
    op.drop_column("pipeline_runs", "input_hash")
