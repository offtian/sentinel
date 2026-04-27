"""
SQLModel table + Pydantic discriminated union for runbook_drift_history (F6.L).

``RunbookDriftHistoryRecord`` is the canonical write shape for drift events
emitted by the daily ``scripts/runbook_drift_check.py`` cron. The record is
event-grain — re-detection within a 24h window of an unresolved row is
suppressed at the persistence layer (see
:mod:`sentinel.domain.runbooks.persistence_drift`); fresh detection events
get fresh rows so an MTTR timeline survives across re-detections.

The ``drift_detail`` JSONB column carries a per-``drift_type`` payload. The
shape contract is enforced app-side via the discriminated union
:data:`DriftDetail`. The DB schema validates only ``drift_type`` membership
(via CHECK constraint); the inner payload is JSONB so new variants can
land without a schema migration. Authoring discipline lives in this module.

See ``docs/superpowers/specs/2026-04-26-f6-runbook-catalog-design.md``
§F6.L for the full design rationale.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel


# ---------------------------------------------------------------------------
# Discriminator literals (kept narrow so the matcher cannot drift them by
# accident — adding a new drift_type requires a migration AND a model entry).
# ---------------------------------------------------------------------------

DriftType = Literal[
    "fixture_failure",
    "min_tag_score_regression",
    "stale_no_matches",
    "tools_yaml_invalid",
    "content_sha_mismatch",
]

DriftSeverity = Literal["low", "medium", "high"]


# ---------------------------------------------------------------------------
# Discriminated union over drift_detail (F6.L.2)
# ---------------------------------------------------------------------------
#
# Each variant pins ``drift_type`` to a single literal so Pydantic's
# tagged-union discriminator can route validation at parse time. The same
# literal feeds the column CHECK constraint in migration 017, so a row
# can only be persisted with a payload whose discriminator matches the
# stored ``drift_type``.


class FixtureFailureDetail(BaseModel):
    """
    Drift payload — a tests.yaml fixture's expected outcome no longer holds.

    ``actual_runbook_id`` is the runbook the matcher returned (which may
    differ from the ``expected_runbook_id`` originally pinned by the
    fixture, or be ``None`` if the matcher returned ``no_match``). The
    pair of (expected, actual) match-method strings is included so the
    operator can tell at a glance whether the regression is from the
    deterministic Stage 1 path or one of the LLM-disambiguator stages.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    drift_type: Literal["fixture_failure"] = "fixture_failure"
    fixture_id: str
    expected_runbook_id: str | None
    actual_runbook_id: str | None
    expected_match_method: str
    actual_match_method: str
    expected_tag_score: int | None
    actual_tag_score: int | None


class MinTagScoreRegressionDetail(BaseModel):
    """
    Drift payload — fixture's ``min_tag_score`` floor regressed.

    Distinct from :class:`FixtureFailureDetail` because the matcher may
    still return the **right** runbook but with a weaker score (e.g.
    after a tag was renamed). The owner is paged at lower severity
    because correctness is unaffected; only confidence has slipped.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    drift_type: Literal["min_tag_score_regression"] = "min_tag_score_regression"
    fixture_id: str
    expected_min: int
    actual_score: int


class StaleNoMatchesDetail(BaseModel):
    """
    Drift payload — runbook hasn't been validated AND has had zero matches.

    Combines the lifecycle ``last_validated`` field with a query against
    ``runbook_match`` to flag runbooks that are dying on the vine: no
    one has touched them, AND the matcher hasn't routed any alert to
    them in the lookback window. Owners can deprecate or refresh.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    drift_type: Literal["stale_no_matches"] = "stale_no_matches"
    last_validated: str  # ISO date string for JSONB stability across drivers
    days_since_validated: int
    lookback_days: int
    match_count_in_lookback: int


class ToolsYamlInvalidDetail(BaseModel):
    """
    Drift payload — a ``tools.yaml`` entry references an unknown tool.

    Severity is high because the matcher would crash on this runbook
    in production once the F7 capability-token enforcement lands —
    the toolset wrapper rejects calls outside the allowed registry,
    so an unknown tool name is a hard fail at investigation time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    drift_type: Literal["tools_yaml_invalid"] = "tools_yaml_invalid"
    missing_tool_names: tuple[str, ...]


class ContentShaMismatchDetail(BaseModel):
    """
    Drift payload — frontmatter content_sha differs from computed sha.

    Caught at CI time by ``scripts/compute_runbook_shas.py --check``
    (F6.E) and at cron time by the drift-detection sweep so the
    operator gets a paged signal even when CI is bypassed. Severity
    is high because the audit row will contain a content_sha that
    no longer corresponds to the runbook actually loaded by the
    matcher (compliance-grade integrity violation per RFC §3.3).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    drift_type: Literal["content_sha_mismatch"] = "content_sha_mismatch"
    frontmatter_sha: str
    computed_sha: str
    mismatched_sections: tuple[str, ...]


DriftDetail = Annotated[
    FixtureFailureDetail
    | MinTagScoreRegressionDetail
    | StaleNoMatchesDetail
    | ToolsYamlInvalidDetail
    | ContentShaMismatchDetail,
    Field(discriminator="drift_type"),
]


# ---------------------------------------------------------------------------
# SQLModel record (F6.L.2)
# ---------------------------------------------------------------------------


class RunbookDriftHistoryRecord(SQLModel, table=True):
    """
    Append-only drift event row (F6 spec §F6.L.1).

    Resolution is patched in-place via ``resolved_at`` / ``resolved_by`` /
    ``resolution_pr_url`` so the event-grain row keeps its detection
    timestamp for MTTR reporting while still indicating closure. The
    partial index ``ix_runbook_drift_history_unresolved`` on
    ``WHERE resolved_at IS NULL`` keeps the dashboard's hot
    open-drift query cheap as the resolved-history grows.

    ``runbook_id`` is **not** a foreign key — runbooks live in
    filesystem-in-git, not in the DB. Indexed for per-runbook digests.

    ``runbook_content_sha`` pins the version the drift was detected
    against; comparing against the current on-disk sha tells the
    operator whether a fix has already shipped (and the open row is
    ready to be marked resolved).
    """

    __tablename__ = "runbook_drift_history"
    __table_args__ = (
        sa.CheckConstraint(
            "drift_type IN ("
            "'fixture_failure', "
            "'min_tag_score_regression', "
            "'stale_no_matches', "
            "'tools_yaml_invalid', "
            "'content_sha_mismatch'"
            ")",
            name="ck_runbook_drift_history_drift_type",
        ),
        sa.CheckConstraint(
            "drift_severity IN ('low', 'medium', 'high')",
            name="ck_runbook_drift_history_drift_severity",
        ),
        Index("ix_runbook_drift_history_runbook_id", "runbook_id"),
        # The DESC order on the "what fired today" query is materialised in
        # the DDL via ``sa.text("detected_at DESC")`` in the migration; the
        # SQLModel index entry mirrors the column-set so the metadata-driven
        # alembic check sees the index without re-asserting ordering.
        Index("ix_runbook_drift_history_detected_at", "detected_at"),
        Index(
            "ix_runbook_drift_history_unresolved",
            "runbook_id",
            "drift_type",
            postgresql_where=sa.text("resolved_at IS NULL"),
        ),
    )

    drift_id: uuid.UUID = SQLField(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), primary_key=True, nullable=False),
    )
    runbook_id: str = SQLField(
        sa_column=Column(sa.String(length=255), nullable=False),
    )
    runbook_content_sha: str = SQLField(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    drift_type: DriftType = SQLField(
        sa_column=Column(sa.String(length=64), nullable=False),
    )
    drift_severity: DriftSeverity = SQLField(
        sa_column=Column(sa.String(length=16), nullable=False),
    )
    drift_detail: dict[str, Any] = SQLField(
        sa_column=Column(JSONB, nullable=False),
    )
    detected_at: datetime = SQLField(
        default_factory=lambda: datetime.now(tz=UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    detected_by: str = SQLField(
        sa_column=Column(sa.String(length=64), nullable=False),
    )
    resolved_at: datetime | None = SQLField(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    resolved_by: str | None = SQLField(
        default=None,
        sa_column=Column(sa.String(length=255), nullable=True),
    )
    resolution_pr_url: str | None = SQLField(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
