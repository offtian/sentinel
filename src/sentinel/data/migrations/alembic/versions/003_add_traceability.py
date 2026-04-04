"""
Add pipeline traceability tables and trace_id correlation columns.

Revision ID: 003
Revises: 002
Create Date: 2026-04-04

Adds:
- trace_id column to investigation_records, ticket_review_records, job_requests
- pipeline_runs table: one row per pipeline execution
- node_executions table: one row per graph node execution
- agent_calls table: one row per PydanticAI agent run
"""

import sqlalchemy as sa
from alembic import op


revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Add trace_id to existing tables --
    op.add_column(
        "investigation_records",
        sa.Column("trace_id", sa.Uuid(), nullable=True, index=True),
    )
    op.add_column(
        "ticket_review_records",
        sa.Column("trace_id", sa.Uuid(), nullable=True, index=True),
    )
    op.add_column(
        "job_requests",
        sa.Column("trace_id", sa.Uuid(), nullable=True, index=True),
    )

    # -- pipeline_runs --
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("pipeline_type", sa.String(), nullable=False),
        sa.Column("job_request_id", sa.Uuid(), nullable=True, index=True),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- node_executions --
    op.create_table(
        "node_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("pipeline_run_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("node_name", sa.String(), nullable=False),
        sa.Column("node_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- agent_calls --
    op.create_table(
        "agent_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("node_execution_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False, server_default=""),
        sa.Column("messages_json", sa.JSON(), nullable=True),
        sa.Column("token_usage_json", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_calls")
    op.drop_table("node_executions")
    op.drop_table("pipeline_runs")
    op.remove_column("job_requests", "trace_id")
    op.remove_column("ticket_review_records", "trace_id")
    op.remove_column("investigation_records", "trace_id")
