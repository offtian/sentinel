"""
Create job queue and audit log tables.

Revision ID: 001
Revises:
Create Date: 2026-03-28

Adds three tables for the async worker infrastructure:
- job_requests: PostgreSQL-backed job queue with FOR UPDATE SKIP LOCKED support
- job_results: Execution outcome records for completed/failed jobs
- audit_log: Append-only regulatory audit trail
"""

import sqlalchemy as sa
from alembic import op


revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- job_requests --
    op.create_table(
        "job_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("requested_by", sa.String(), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("locked_by", sa.String(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index("ix_job_requests_status", "job_requests", ["status"])
    op.create_index(
        "ix_job_requests_status_priority",
        "job_requests",
        ["status", "priority", "created_at"],
    )
    op.create_unique_constraint(
        "uq_job_requests_idempotency_key",
        "job_requests",
        ["idempotency_key"],
    )

    # -- job_results --
    op.create_table(
        "job_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_request_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- audit_log --
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False, index=True),
        sa.Column("resource_type", sa.String(), nullable=False, index=True),
        sa.Column("resource_id", sa.String(), nullable=False, index=True),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("job_results")
    op.drop_table("job_requests")
