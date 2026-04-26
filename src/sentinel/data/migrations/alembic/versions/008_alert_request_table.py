"""
Create alert_request canonical table (RFC 12.3.1).

Revision ID: 008
Revises: 007
Create Date: 2026-04-26

Adds the canonical alert ingestion row written by the dedup stage of the SRE
pipeline. The ``request_id`` UUID is the envelope identifier propagated to
spans, the ``runbook_match`` table, and downstream investigation rows.

Columns:
- request_id              UUID         primary key
- tenant_id               varchar      not null (B-tree indexed)
- received_at             timestamptz  not null  default now-on-write
- provider                text         not null  (Literal["pagerduty", "datadog", "alertmanager"])
- alert_id                varchar      not null
- severity                varchar      not null
- redacted_annotations    JSONB        nullable
- dedup_status            text         not null  (Literal["new", "duplicate"])

Indexes (RFC 12.4):
- ix_alert_request_tenant_id          (tenant_id,)                    single-col tenant lookup
- ix_alert_request_tenant_received    (tenant_id, received_at DESC)   per-tenant timeline reads
- ix_alert_request_provider_alert_id  (provider, alert_id)            dedup lookups
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_request",
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("alert_id", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("redacted_annotations", postgresql.JSONB(), nullable=True),
        sa.Column("dedup_status", sa.Text(), nullable=False),
    )
    op.create_index("ix_alert_request_tenant_id", "alert_request", ["tenant_id"])
    op.create_index(
        "ix_alert_request_tenant_received",
        "alert_request",
        ["tenant_id", sa.text("received_at DESC")],
    )
    op.create_index(
        "ix_alert_request_provider_alert_id",
        "alert_request",
        ["provider", "alert_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_request_provider_alert_id", table_name="alert_request")
    op.drop_index("ix_alert_request_tenant_received", table_name="alert_request")
    op.drop_index("ix_alert_request_tenant_id", table_name="alert_request")
    op.drop_table("alert_request")
