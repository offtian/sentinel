"""
Create runbook_match canonical table (RFC 12.3.2).

Revision ID: 009
Revises: 008
Create Date: 2026-04-26

Adds the runbook-match decision row tied to each alert_request. The match
table records which runbook was selected, the match method (tag-based, RAG,
or generic fallback), the match confidence, and the truncated content-hash
version of the runbook bytes.

Columns:
- match_id              UUID         primary key
- request_id            UUID         not null   FK -> alert_request.request_id
                                                (constraint: fk_runbook_match_alert_request)
- runbook_id            text         not null
- runbook_version_sha   varchar(32)  not null   (truncated sha256 of runbook bytes)
- match_method          text         not null   (Literal["tag", "rag", "generic_fallback"])
- match_confidence      float        not null
- matched_at            timestamptz  not null   default now-on-write
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runbook_match",
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("runbook_id", sa.Text(), nullable=False),
        sa.Column("runbook_version_sha", sa.String(length=32), nullable=False),
        sa.Column("match_method", sa.Text(), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=False),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["alert_request.request_id"],
            name="fk_runbook_match_alert_request",
        ),
    )


def downgrade() -> None:
    op.drop_table("runbook_match")
