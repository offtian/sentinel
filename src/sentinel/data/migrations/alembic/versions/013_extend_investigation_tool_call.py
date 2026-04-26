"""
Extend investigation_records + agent_calls toward RFC investigation/tool_call shape (RFC 12.3.4 + 12.3.6).

Revision ID: 013
Revises: 012
Create Date: 2026-04-26

This migration combines F3.7 + F3.8 of the foundations plan into a single
upgrade/downgrade pair. The two tables move toward (but do not yet match)
their RFC §12.3.4 (investigation) and §12.3.6 (tool_call) canonical shapes.
No existing columns are renamed or removed; foundations is schema-only and
no data is backfilled (writers land in later slices).

investigation_records column adds (RFC 12.3.4):
- request_id          UUID         nullable indexed   FK -> alert_request.request_id
                                                       (constraint: fk_investigation_alert_request)
                                                       Nullable because rows pre-RFC carry no envelope.
- runbook_match_id    UUID         nullable           FK -> runbook_match.match_id
                                                       (constraint: fk_investigation_runbook_match)
                                                       Nullable; runbook-match wiring lands in F6.
- model_id_primary    text         nullable           Foundations writers populate from agent context.
- iteration_count     integer      not null           server_default "0" so existing rows backfill.
- terminated_reason   text         nullable           Set only when the loop terminates early.
- loop_cap_hit        boolean      not null           server_default false so existing rows backfill.

agent_calls column adds (RFC 12.3.6):
- tool_name             text         nullable        Set only when the call wraps a tool invocation.
- capability_token      text         nullable        Foundations writers fill in F7.
- evidence_object_ids   JSONB        nullable        List of object-store keys for evidence.
- succeeded             boolean      nullable        Existing rows have no success signal until backfill.
- tenant_id             text         nullable        Indexed (ix_agent_calls_tenant_id) for per-tenant
                                                     tool-call lookups; foundations writers populate from
                                                     the active envelope.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- F3.7: investigation_records column adds (RFC 12.3.4) --
    op.add_column(
        "investigation_records",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_investigation_records_request_id",
        "investigation_records",
        ["request_id"],
    )
    op.create_foreign_key(
        "fk_investigation_alert_request",
        "investigation_records",
        "alert_request",
        ["request_id"],
        ["request_id"],
    )
    op.add_column(
        "investigation_records",
        sa.Column("runbook_match_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_investigation_runbook_match",
        "investigation_records",
        "runbook_match",
        ["runbook_match_id"],
        ["match_id"],
    )
    op.add_column(
        "investigation_records",
        sa.Column("model_id_primary", sa.Text(), nullable=True),
    )
    op.add_column(
        "investigation_records",
        sa.Column(
            "iteration_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "investigation_records",
        sa.Column("terminated_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "investigation_records",
        sa.Column(
            "loop_cap_hit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # -- F3.8: agent_calls column adds (RFC 12.3.6) --
    op.add_column(
        "agent_calls",
        sa.Column("tool_name", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_calls",
        sa.Column("capability_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_calls",
        sa.Column("evidence_object_ids", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "agent_calls",
        sa.Column("succeeded", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "agent_calls",
        sa.Column("tenant_id", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_agent_calls_tenant_id",
        "agent_calls",
        ["tenant_id"],
    )


def downgrade() -> None:
    # -- F3.8 reverse: drop agent_calls index then columns (reverse-add order) --
    op.drop_index("ix_agent_calls_tenant_id", table_name="agent_calls")
    op.drop_column("agent_calls", "tenant_id")
    op.drop_column("agent_calls", "succeeded")
    op.drop_column("agent_calls", "evidence_object_ids")
    op.drop_column("agent_calls", "capability_token")
    op.drop_column("agent_calls", "tool_name")

    # -- F3.7 reverse: drop FKs first, then index, then columns (reverse-add order) --
    op.drop_constraint(
        "fk_investigation_runbook_match",
        "investigation_records",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_investigation_alert_request",
        "investigation_records",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_investigation_records_request_id",
        table_name="investigation_records",
    )
    op.drop_column("investigation_records", "loop_cap_hit")
    op.drop_column("investigation_records", "terminated_reason")
    op.drop_column("investigation_records", "iteration_count")
    op.drop_column("investigation_records", "model_id_primary")
    op.drop_column("investigation_records", "runbook_match_id")
    op.drop_column("investigation_records", "request_id")
