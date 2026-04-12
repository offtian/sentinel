"""
Add agent_prompts_json to pipeline_runs for multi-agent prompt capture.

Revision ID: 005
Revises: 004
Create Date: 2026-04-12

Adds to pipeline_runs:
- agent_prompts_json: JSON array of per-agent prompt metadata
"""

import sqlalchemy as sa
from alembic import op


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("agent_prompts_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "agent_prompts_json")
