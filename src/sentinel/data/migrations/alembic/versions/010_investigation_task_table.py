"""
Create investigation_task + task_status_change canonical tables (RFC 12.3.7).

Revision ID: 010
Revises: 009
Create Date: 2026-04-26

Adds the investigation task list and its status-change audit trail. Each
task row breaks an investigation down into a single trackable step; the
status-change row records each lifecycle transition the task undergoes.

investigation_task columns:
- task_id            UUID         primary key
- investigation_id   UUID         not null   FK -> investigation_records.id
                                              (constraint: fk_investigation_task_investigation)
- task_text          text         not null
- created_at         timestamptz  not null   default now-on-write
- completed_at       timestamptz  nullable   (open tasks have no completion timestamp)
- evidence_refs      JSONB        nullable   (foundations slice keeps this open-shaped)

task_status_change columns:
- id                 UUID         primary key
- task_id            UUID         not null   FK -> investigation_task.task_id
                                              (constraint: fk_task_status_change_task)
- from_status        text         nullable   (the first transition has no prior status)
- to_status          text         not null
- at                 timestamptz  not null   default now-on-write
- reason             text         nullable
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigation_task",
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("task_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigation_records.id"],
            name="fk_investigation_task_investigation",
        ),
    )
    op.create_table(
        "task_status_change",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["investigation_task.task_id"],
            name="fk_task_status_change_task",
        ),
    )


def downgrade() -> None:
    op.drop_table("task_status_change")
    op.drop_table("investigation_task")
