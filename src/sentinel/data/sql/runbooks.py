"""
SQLModel table definitions for runbook_match (RFC 12.3.2) and runbook_feedback (F6 §8.2).

``RunbookMatchRecord`` records the runbook chosen for a given alert_request,
including the match method (deterministic tag, LLM disambiguator on ties,
LLM zero-match rescue, alphabetical fallback, or explicit no-match), the
confidence score, the truncated content-hash version of the runbook bytes
that was matched, and — for regulator audit — the full top-k candidate
tuples considered by the matcher (RFC §3.3). The ``request_id`` foreign key
ties each match back to its envelope row in ``alert_request``.

A row is written on **every** match attempt, including no-match outcomes:
``runbook_id`` and ``runbook_version_sha`` are nullable so the no-match path
can persist its candidate audit trail without inventing a fake winner.

``RunbookFeedbackRecord`` captures 👍 / 👎 / wrong-runbook signals from the
F8 approval gate against a specific ``(runbook_id, runbook_content_sha)``
pair. The table is the regulator-audit source for "did humans agree with
the matcher?" per RFC §3.3 and feeds the runbook-owner weekly digest in a
follow-on plan.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, Float, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field, SQLModel


MatchMethod = Literal[
    "tag",
    "rag",
    "generic_fallback",
    "llm_disambiguator_tie",
    "llm_zero_match_rescue",
    "no_match",
    "alphabetical_fallback",
]

FeedbackSentiment = Literal["positive", "negative", "wrong_runbook"]


class RunbookMatchRecord(SQLModel, table=True):
    """
    Runbook match decision row (RFC 12.3.2 + F6 spec §5.5 / §8.1).

    The ``request_id`` FK links the match to the canonical alert envelope
    row in ``alert_request``. ``runbook_version_sha`` is the truncated
    sha256 of the runbook bytes carried over from F3; F6 adds
    ``runbook_content_sha`` (the canonicalised quartet hash defined in
    spec §4.2) alongside the LLM-disambiguator audit fields and the
    full top-k ``candidates_json`` answer to "why this runbook and
    not another?".

    Both ``runbook_id`` and ``runbook_version_sha`` are nullable so that
    the F6 no-match path (Stage 2B returns ``no_match``) can still persist
    its audit row without inventing a fake winner.
    """

    __tablename__ = "runbook_match"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["alert_request.request_id"],
            name="fk_runbook_match_alert_request",
        ),
    )

    match_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    request_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    runbook_id: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    runbook_version_sha: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=32), nullable=True),
    )
    match_method: MatchMethod = Field(sa_column=Column(Text, nullable=False))
    match_confidence: float = Field(sa_column=Column(Float, nullable=False))
    matched_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    runbook_content_sha: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=32), nullable=True),
    )
    tag_score: int | None = Field(
        default=None,
        sa_column=Column(sa.Integer, nullable=True),
    )
    llm_choice: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=255), nullable=True),
    )
    llm_justification: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    candidates_json: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )


class RunbookFeedbackRecord(SQLModel, table=True):
    """
    Runbook feedback row (F6 spec §6.4 / §8.2).

    Captures 👍 / 👎 / wrong-runbook signals from the F8 approval gate
    against a specific ``(runbook_id, runbook_content_sha)`` pair. The
    ``request_id`` FK links each feedback row back to the alert envelope
    that triggered the match, so the weekly digest can group feedback by
    runbook owner and by content version.

    ``runbook_id`` is **not** a foreign key (runbooks are filesystem-in-git,
    not in the DB); it is informational and indexed for per-runbook digest
    queries. ``runbook_content_sha`` is the truncated sha256[:32] of the
    runbook quartet at the time the feedback was given, so post-edit drift
    can be detected by comparing against the current on-disk content sha.
    """

    __tablename__ = "runbook_feedback"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["alert_request.request_id"],
            name="fk_runbook_feedback_alert_request",
        ),
        Index("ix_runbook_feedback_runbook_id", "runbook_id"),
        Index("ix_runbook_feedback_request_id", "request_id"),
    )

    feedback_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    request_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), nullable=False),
    )
    runbook_id: str = Field(
        sa_column=Column(sa.String(length=255), nullable=False),
    )
    runbook_content_sha: str = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    sentiment: FeedbackSentiment = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    reason: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    submitted_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    submitted_by: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=255), nullable=True),
    )
