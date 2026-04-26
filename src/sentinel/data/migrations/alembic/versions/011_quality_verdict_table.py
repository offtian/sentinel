"""
Create quality_verdict + approval_record canonical tables (RFC 12.3.8).

Revision ID: 011
Revises: 010
Create Date: 2026-04-26

Adds the quality verdict row (groundedness, evidence-ref count, confidence
score, verdict reason) and the approval row that records human-in-the-loop
decisions against a verdict.

quality_verdict columns:
- verdict_id          UUID         primary key
- investigation_id    UUID         not null   FK -> investigation_records.id
                                               (constraint: fk_quality_verdict_investigation)
- groundedness_pass   bool         not null
- evidence_ref_count  integer      not null
- confidence_score    float        not null
- verdict_reason      text         not null
- assessed_at         timestamptz  not null   default now-on-write

approval_record columns:
- id                  UUID         primary key
- verdict_id          UUID         not null   FK -> quality_verdict.verdict_id
                                               (constraint: fk_approval_record_verdict)
- approver            text         not null
- decision            text         not null   (plain text, not Postgres ENUM)
- decided_at          timestamptz  not null   default now-on-write
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quality_verdict",
        sa.Column(
            "verdict_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("groundedness_pass", sa.Boolean(), nullable=False),
        sa.Column("evidence_ref_count", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("verdict_reason", sa.Text(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigation_records.id"],
            name="fk_quality_verdict_investigation",
        ),
    )
    op.create_table(
        "approval_record",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "verdict_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("approver", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["verdict_id"],
            ["quality_verdict.verdict_id"],
            name="fk_approval_record_verdict",
        ),
    )


def downgrade() -> None:
    op.drop_table("approval_record")
    op.drop_table("quality_verdict")
