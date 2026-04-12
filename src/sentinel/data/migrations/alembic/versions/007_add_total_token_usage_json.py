"""
Add total_token_usage_json to pipeline_runs for aggregate cost tracking.

Revision ID: 007
Revises: 006
Create Date: 2026-04-12

Adds to pipeline_runs:
- total_token_usage_json: JSON aggregate of token usage and cost across all agent calls
"""

import sqlalchemy as sa
from alembic import op


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("total_token_usage_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "total_token_usage_json")
