"""
Create comparison_runs and eval_runs tables.

Revision ID: 002
Revises: 001
Create Date: 2026-04-03

Adds two tables for investigation comparison and evaluation tracking:
- comparison_runs: side-by-side investigation backend results
- eval_runs: evaluation framework execution records
"""

import sqlalchemy as sa
from alembic import op


revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- comparison_runs --
    op.create_table(
        "comparison_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("investigation_record_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("baseline_adapter", sa.String(), nullable=False),
        sa.Column("challenger_adapter", sa.String(), nullable=False),
        sa.Column("baseline_result_json", sa.JSON(), nullable=False),
        sa.Column("challenger_result_json", sa.JSON(), nullable=False),
        sa.Column("comparison_result_json", sa.JSON(), nullable=False),
        sa.Column("baseline_duration_ms", sa.Integer(), nullable=False),
        sa.Column("challenger_duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- eval_runs --
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dataset_name", sa.String(), nullable=False, index=True),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("passed_cases", sa.Integer(), nullable=False),
        sa.Column("failed_cases", sa.Integer(), nullable=False),
        sa.Column("average_score", sa.Float(), nullable=True),
        sa.Column("results_json", sa.JSON(), nullable=False),
        sa.Column("run_duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_table("comparison_runs")
