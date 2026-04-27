"""
Add F6 runbook_match extensions + new runbook_feedback table (RFC §3.3 + F6 spec §8).

Revision ID: 015
Revises: 014
Create Date: 2026-04-26

Two-part schema change for the F6 runbook catalog:

1. ``runbook_match`` extensions (RFC §3.3 regulator-explainable audit row):
   the matcher now writes a row on **every** attempt — including no-match —
   carrying the full top-k candidate audit trail and the LLM
   disambiguator's choice + justification + content hash.

   - runbook_content_sha   varchar(32)   nullable   sha256[:32] of the
                                                    canonicalised runbook
                                                    quartet (RUNBOOK.md body
                                                    + tools.yaml + checks.yaml
                                                    + tests.yaml) — F6 spec
                                                    §4.2. Truncated for
                                                    storage; collision
                                                    probability negligible at
                                                    10^4 runbooks.
   - tag_score             integer       nullable   Stage 1 deterministic
                                                    tag overlap count; null
                                                    on rescue / no-match
                                                    paths where Stage 1
                                                    yielded nothing.
   - llm_choice            varchar(255)  nullable   runbook_id chosen by
                                                    Stage 2 LLM, or the
                                                    literal "no_match".
   - llm_justification     text          nullable   ≤200-char single-line
                                                    rationale emitted by
                                                    Stage 2 LLM.
   - candidates_json       JSONB         nullable   Top-k candidate tuples
                                                    -- [{"runbook_id": ...,
                                                    "content_sha": ...,
                                                    "tag_score": ...,
                                                    "matched_via": ...}].
                                                    Always populated for
                                                    regulator audit per
                                                    RFC §3.3.

   In addition, ``runbook_id`` and ``runbook_version_sha`` become NULLABLE.
   No-match rows have neither; pre-F6 rows backfill cleanly because both
   columns previously held non-null values and the relax preserves them.

   The Python-side ``MatchMethod`` Literal widens to include the new
   variants ``llm_disambiguator_tie``, ``llm_zero_match_rescue``,
   ``no_match``, ``alphabetical_fallback``. ``match_method`` is stored as
   ``text`` (no Postgres CHECK constraint exists in the DB — the constraint
   is enforced by the SQLModel Literal at write time per the RFC §15.4
   shape-not-storage convention used elsewhere in this schema). No DB
   action is required for the widening.

2. New ``runbook_feedback`` table (F6 spec §8.2): captures 👍 / 👎 /
   wrong-runbook signals from the F8 approval gate against a specific
   ``(runbook_id, runbook_content_sha)`` pair. ``runbook_id`` is **not**
   a foreign key (runbooks are filesystem-in-git, not in the DB); it is
   informational and indexed for per-runbook digest queries.

All new ``runbook_match`` columns are nullable for rolling-deploy safety:
pre-F6 rows simply carry NULL until they are re-matched.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- F6.D.1: runbook_match — relax legacy NOT NULLs for no-match rows --
    # ``runbook_id`` and ``runbook_version_sha`` were NOT NULL in 009; F6's
    # always-write audit row needs them nullable so the no-match path can
    # persist its candidates_json without inventing a fake winner.
    op.alter_column(
        "runbook_match",
        "runbook_id",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "runbook_match",
        "runbook_version_sha",
        existing_type=sa.String(length=32),
        nullable=True,
    )

    # -- F6.D.1: runbook_match — F6 audit + LLM disambiguator columns --
    op.add_column(
        "runbook_match",
        sa.Column("runbook_content_sha", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "runbook_match",
        sa.Column("tag_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "runbook_match",
        sa.Column("llm_choice", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "runbook_match",
        sa.Column("llm_justification", sa.Text(), nullable=True),
    )
    op.add_column(
        "runbook_match",
        sa.Column("candidates_json", postgresql.JSONB(), nullable=True),
    )

    # NOTE: ``match_method`` is stored as TEXT (009_runbook_match_table.py)
    # with the accepted vocabulary enforced by the SQLModel ``MatchMethod``
    # Literal at write time. F6 widens that Literal to add
    # ``llm_disambiguator_tie``, ``llm_zero_match_rescue``, ``no_match``,
    # and ``alphabetical_fallback``. No CHECK constraint exists in the DB
    # so no DB action is required for the widening.

    # -- F6.D.1: runbook_feedback — new table per spec §8.2 --
    op.create_table(
        "runbook_feedback",
        sa.Column(
            "feedback_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("runbook_id", sa.String(length=255), nullable=False),
        sa.Column("runbook_content_sha", sa.String(length=32), nullable=False),
        sa.Column("sentiment", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_by", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "sentiment IN ('positive', 'negative', 'wrong_runbook')",
            name="ck_runbook_feedback_sentiment",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["alert_request.request_id"],
            name="fk_runbook_feedback_alert_request",
        ),
    )
    op.create_index(
        "ix_runbook_feedback_runbook_id",
        "runbook_feedback",
        ["runbook_id"],
    )
    op.create_index(
        "ix_runbook_feedback_request_id",
        "runbook_feedback",
        ["request_id"],
    )


def downgrade() -> None:
    # -- F6.D.1 reverse: drop runbook_feedback indexes then table --
    op.drop_index("ix_runbook_feedback_request_id", table_name="runbook_feedback")
    op.drop_index("ix_runbook_feedback_runbook_id", table_name="runbook_feedback")
    op.drop_table("runbook_feedback")

    # -- F6.D.1 reverse: drop runbook_match F6 columns (reverse-add order) --
    op.drop_column("runbook_match", "candidates_json")
    op.drop_column("runbook_match", "llm_justification")
    op.drop_column("runbook_match", "llm_choice")
    op.drop_column("runbook_match", "tag_score")
    op.drop_column("runbook_match", "runbook_content_sha")

    # -- F6.D.1 reverse: restore runbook_match legacy NOT NULL columns --
    # CAVEAT: this re-tightening is only safe if no rows currently carry
    # NULL ``runbook_id`` / ``runbook_version_sha``. Once F6 starts writing
    # no-match audit rows (which carry NULL for both), running this
    # downgrade against a populated table will fail. For dev / test envs
    # this is the intended behaviour — back the data out before downgrading,
    # or recreate the database from the prior revision.
    op.alter_column(
        "runbook_match",
        "runbook_version_sha",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.alter_column(
        "runbook_match",
        "runbook_id",
        existing_type=sa.Text(),
        nullable=False,
    )
