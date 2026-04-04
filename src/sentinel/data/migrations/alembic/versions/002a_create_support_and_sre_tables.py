"""
Create support and SRE persistence tables.

Revision ID: 002a
Revises: 002
Create Date: 2026-04-04

Adds:
- investigation_records: persisted SRE investigation outputs
- ticket_review_records: persisted support ticket review outputs
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "002a"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigation_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("alert_source", sa.String(), nullable=False),
        sa.Column("alert_id", sa.String(), nullable=False),
        sa.Column("alert_title", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("remediation", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("findings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_investigation_records_alert_id",
        "investigation_records",
        ["alert_id"],
    )

    op.create_table(
        "ticket_review_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("ticket_key", sa.String(), nullable=False),
        sa.Column("suggested_response", sa.Text(), nullable=False),
        sa.Column("sources_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="drafted"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ticket_review_records_ticket_id",
        "ticket_review_records",
        ["ticket_id"],
    )
    op.create_index(
        "ix_ticket_review_records_ticket_key",
        "ticket_review_records",
        ["ticket_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_review_records_ticket_key", table_name="ticket_review_records")
    op.drop_index("ix_ticket_review_records_ticket_id", table_name="ticket_review_records")
    op.drop_table("ticket_review_records")
    op.drop_index("ix_investigation_records_alert_id", table_name="investigation_records")
    op.drop_table("investigation_records")
