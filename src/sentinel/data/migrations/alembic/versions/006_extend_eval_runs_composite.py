"""
Extend eval_runs for composite scoring and per-evaluator assertions.

Revision ID: 006
Revises: 005
Create Date: 2026-04-12

Adds to eval_runs:
- agent_name: VARCHAR, nullable, indexed — identifies the agent under evaluation
- composite_score: FLOAT, nullable — weighted composite from metrics
- assertion_details_json: JSON, nullable — per-evaluator key/value breakdown
"""

import sqlalchemy as sa
from alembic import op


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "eval_runs",
        sa.Column("agent_name", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_eval_runs_agent_name",
        "eval_runs",
        ["agent_name"],
    )
    op.add_column(
        "eval_runs",
        sa.Column("composite_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "eval_runs",
        sa.Column("assertion_details_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("eval_runs", "assertion_details_json")
    op.drop_column("eval_runs", "composite_score")
    op.drop_index("ix_eval_runs_agent_name", table_name="eval_runs")
    op.drop_column("eval_runs", "agent_name")
