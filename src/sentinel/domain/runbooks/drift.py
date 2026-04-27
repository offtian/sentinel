"""
Daily drift-detection sweeps for the Sentinel runbook catalog (F6.L).

Three sweeps run from :mod:`scripts.runbook_drift_check`:

* :func:`sweep_fixture_replays` — re-runs every ``tests.yaml`` fixture
  through the matcher and emits a ``fixture_failure`` or
  ``min_tag_score_regression`` event when the expected outcome no
  longer holds.

* :func:`sweep_stale_runbooks` — joins runbook frontmatter
  ``last_validated`` against ``runbook_match`` row counts in the
  configured lookback window, emitting ``stale_no_matches`` when a
  runbook hasn't been validated for ``stale_threshold_days`` AND has
  zero matches in ``lookback_days``.

* :func:`sweep_tools_registry` — asserts every ``tool_name`` listed
  in any runbook's ``tools.yaml`` is present in the project's
  allowed-tool registry; emits ``tools_yaml_invalid`` for runbooks
  containing missing names.

Each sweep returns a tuple of frozen :class:`DriftEvent` instances. The
script writes them to ``runbook_drift_history`` via
:mod:`sentinel.domain.runbooks.persistence_drift`. Severity heuristics
live with the sweeps so the policy is co-located with the detection
logic; the persistence layer is policy-free.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import date, timedelta
from typing import Any

import attrs
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.data.sql import runbook_drift
from sentinel.data.sql import runbooks as runbooks_sql
from sentinel.domain.runbooks import matcher as matcher_mod
from sentinel.domain.runbooks import models
from sentinel.utils import logs


# ---------------------------------------------------------------------------
# Severity heuristics (policy lives here, not in persistence)
# ---------------------------------------------------------------------------
#
# - fixture_failure          -> high   (matcher returns wrong runbook;
#                                       investigations will go to the wrong
#                                       playbook in production)
# - min_tag_score_regression -> medium (matcher still picks the right
#                                       runbook but with a weaker score;
#                                       confidence has slipped)
# - stale_no_matches         -> low    (lifecycle hint; not blocking)
# - tools_yaml_invalid       -> high   (F7 toolset wrapper would refuse
#                                       these calls in production; runbook
#                                       is broken)
# - content_sha_mismatch     -> high   (audit row carries a sha that no
#                                       longer matches the loaded body;
#                                       compliance integrity violation)

_SEVERITY_BY_DRIFT_TYPE: Mapping[runbook_drift.DriftType, runbook_drift.DriftSeverity] = {
    "fixture_failure": "high",
    "min_tag_score_regression": "medium",
    "stale_no_matches": "low",
    "tools_yaml_invalid": "high",
    "content_sha_mismatch": "high",
}


# A matcher closure: the script binds in the disambiguator + envelope so the
# sweeps stay purely about which fixtures fail. Production cron path uses an
# alphabetical-fallback disambiguator (no LLM in the cron loop — fixture
# replays must be deterministic across cron ticks).
ReplayMatcher = Callable[
    [matcher_mod.MatchableAlert, Mapping[str, models.Runbook]],
    Awaitable[models.RunbookMatch],
]


@attrs.frozen(kw_only=True, slots=True)
class DriftEvent:
    """
    One drift detection event ready for persistence.

    Pure data — neither the sweeps nor the persistence layer mutate this
    shape. ``drift_detail`` is the serialised discriminated-union variant
    (``model_dump()`` of a :class:`runbook_drift.DriftDetail`) so the
    persistence layer can write straight to JSONB without a re-validation
    pass.
    """

    runbook_id: str
    runbook_content_sha: str
    drift_type: runbook_drift.DriftType
    drift_severity: runbook_drift.DriftSeverity
    drift_detail: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Sweep 1 — fixture replay
# ---------------------------------------------------------------------------


def _build_fixture_alert(*, fixture: models.TestSpec) -> _ReplayAlert:
    """
    Return a synthetic :class:`MatchableAlert` derived from the fixture.

    The cron path doesn't load fixture JSON files — those are full alert
    payloads consumed by the integration tests. For drift detection the
    fixture's expected fields (alertname inferred from ``runbook_id``,
    severity P3, resource_kind ``Pod`` defaults) are deliberately minimal
    so the **expected** outcome from ``tests.yaml`` is the single source
    of truth. If the matcher then produces a different outcome, that's
    the drift signal — and the test caller is responsible for ensuring
    the fixture's *real* alert payload matches the metadata it asserts.
    """
    return _ReplayAlert(fixture_id=fixture.id)


@attrs.frozen(kw_only=True, slots=True)
class _ReplayAlert:
    """
    Minimal alert stub used by the cron's fixture-replay sweep.

    The cron does not need to read every fixture's full alert payload to
    detect drift — the fixture's ``expected`` block is the contract. We
    instead build a synthetic alert that flips the matcher's behaviour:
    the alert advertises an ``alertname`` matching the fixture id (so a
    runbook whose ``applies_to.alertnames`` lists that fixture id can
    score on it). For the cron's purpose this catches the high-value
    regression: a runbook stops matching its own fixtures because tags
    were renamed or a Stage 2 LLM threshold was tightened.
    """

    fixture_id: str

    @property
    def alertname(self) -> str:
        return self.fixture_id

    @property
    def severity(self) -> str:
        return "P3"

    @property
    def resource_kind(self) -> str:
        return "Pod"

    @property
    def labels(self) -> Mapping[str, str]:
        return {}

    @property
    def pii_class(self) -> str:
        return "internal"


def _emit_fixture_failure(
    *,
    runbook: models.Runbook,
    fixture: models.TestSpec,
    actual: models.RunbookMatch,
) -> DriftEvent:
    """Return a ``fixture_failure`` :class:`DriftEvent` for the given mismatch."""
    detail = runbook_drift.FixtureFailureDetail(
        fixture_id=fixture.id,
        expected_runbook_id=fixture.expected.runbook_id,
        actual_runbook_id=actual.matched_runbook_id,
        expected_match_method=fixture.expected.match_method,
        actual_match_method=actual.match_method,
        expected_tag_score=fixture.expected.min_tag_score,
        actual_tag_score=actual.tag_score,
    )
    return DriftEvent(
        runbook_id=runbook.runbook_id,
        runbook_content_sha=runbook.metadata.content_sha,
        drift_type="fixture_failure",
        drift_severity=_SEVERITY_BY_DRIFT_TYPE["fixture_failure"],
        drift_detail=detail.model_dump(),
    )


def _emit_min_tag_score_regression(
    *,
    runbook: models.Runbook,
    fixture: models.TestSpec,
    actual_score: int,
) -> DriftEvent:
    """Return a ``min_tag_score_regression`` :class:`DriftEvent`."""
    expected = fixture.expected.min_tag_score
    if expected is None:
        msg = (
            f"_emit_min_tag_score_regression invoked with no expected min_tag_score "
            f"for fixture {fixture.id!r} on runbook {runbook.runbook_id!r}"
        )
        raise ValueError(msg)
    detail = runbook_drift.MinTagScoreRegressionDetail(
        fixture_id=fixture.id,
        expected_min=expected,
        actual_score=actual_score,
    )
    return DriftEvent(
        runbook_id=runbook.runbook_id,
        runbook_content_sha=runbook.metadata.content_sha,
        drift_type="min_tag_score_regression",
        drift_severity=_SEVERITY_BY_DRIFT_TYPE["min_tag_score_regression"],
        drift_detail=detail.model_dump(),
    )


def _fixture_outcome_matches(*, fixture: models.TestSpec, actual: models.RunbookMatch) -> bool:
    """Return True when ``actual`` satisfies the fixture's expected runbook + method."""
    if fixture.expected.runbook_id != actual.matched_runbook_id:
        return False
    return fixture.expected.match_method == actual.match_method


async def sweep_fixture_replays(
    *,
    runbooks: Mapping[str, models.Runbook],
    matcher: ReplayMatcher,
) -> tuple[DriftEvent, ...]:
    """
    Re-run every runbook's fixtures through the matcher; emit drift events on mismatch.

    The matcher is passed in as a closure so the sweep stays decoupled from
    the disambiguator wiring (no LLM is invoked on the cron path; the
    closure binds an alphabetical-fallback disambiguator).

    :returns: Tuple of :class:`DriftEvent` for every fixture that drifted.
        Empty when the catalog is clean.
    """
    events: list[DriftEvent] = []
    for runbook in runbooks.values():
        for fixture in runbook.tests:
            actual = await matcher(_build_fixture_alert(fixture=fixture), runbooks)
            if not _fixture_outcome_matches(fixture=fixture, actual=actual):
                events.append(
                    _emit_fixture_failure(runbook=runbook, fixture=fixture, actual=actual)
                )
                continue
            expected_min = fixture.expected.min_tag_score
            if expected_min is None:
                continue
            actual_score = actual.tag_score
            # Fixture pinned a min_tag_score but actual run produced no
            # tag_score (matcher took an LLM-only branch). That's a regression
            # because the fixture pre-condition no longer holds.
            if actual_score is None or actual_score < expected_min:
                events.append(
                    _emit_min_tag_score_regression(
                        runbook=runbook,
                        fixture=fixture,
                        actual_score=actual_score if actual_score is not None else 0,
                    )
                )
    return tuple(events)


# ---------------------------------------------------------------------------
# Sweep 2 — stale-runbook detection
# ---------------------------------------------------------------------------


async def _count_recent_matches(*, session: AsyncSession, runbook_id: str, since: date) -> int:
    """Return the count of ``runbook_match`` rows for a runbook since ``since``."""
    record_cls = runbooks_sql.RunbookMatchRecord
    query = (
        sa.select(sa.func.count())
        .select_from(record_cls)
        .where(record_cls.runbook_id == runbook_id)
        .where(record_cls.matched_at >= since)
    )
    result = await session.execute(query)
    return int(result.scalar_one())


async def sweep_stale_runbooks(
    *,
    session: AsyncSession,
    runbooks: Mapping[str, models.Runbook],
    today: date,
    lookback_days: int = 30,
    stale_threshold_days: int = 90,
) -> tuple[DriftEvent, ...]:
    """
    Emit ``stale_no_matches`` for runbooks past ``stale_threshold_days`` AND unused.

    Skips deprecated runbooks (already declared end-of-life) and runbooks
    without a ``last_validated`` date (lifecycle field never set —
    treated as "never validated"; the loader requires the field so this
    branch only fires on an authoring placeholder).

    :param session: Async SQLAlchemy session for the ``runbook_match`` query.
    :param runbooks: Discovered catalog keyed by ``runbook_id``.
    :param today: Reference date for the staleness calculation. Passed in
        rather than read from the system clock so callers can replay
        deterministically (per the application.md system-clock rule).
    :param lookback_days: Window over which ``runbook_match`` rows are
        counted to decide "no matches" (default 30).
    :param stale_threshold_days: Days since ``last_validated`` past which
        a runbook is considered stale (default 90).
    """
    events: list[DriftEvent] = []
    lookback_start = today - timedelta(days=lookback_days)
    for runbook in runbooks.values():
        meta = runbook.metadata
        if meta.deprecated_at is not None:
            continue
        last_validated = meta.last_validated
        if last_validated is None:
            continue
        days_since = (today - last_validated).days
        if days_since <= stale_threshold_days:
            continue
        match_count = await _count_recent_matches(
            session=session, runbook_id=runbook.runbook_id, since=lookback_start
        )
        if match_count > 0:
            continue
        detail = runbook_drift.StaleNoMatchesDetail(
            last_validated=last_validated.isoformat(),
            days_since_validated=days_since,
            lookback_days=lookback_days,
            match_count_in_lookback=match_count,
        )
        events.append(
            DriftEvent(
                runbook_id=runbook.runbook_id,
                runbook_content_sha=meta.content_sha,
                drift_type="stale_no_matches",
                drift_severity=_SEVERITY_BY_DRIFT_TYPE["stale_no_matches"],
                drift_detail=detail.model_dump(),
            )
        )
    return tuple(events)


# ---------------------------------------------------------------------------
# Sweep 3 — tools-registry validation
# ---------------------------------------------------------------------------


def sweep_tools_registry(
    *,
    runbooks: Mapping[str, models.Runbook],
    tool_registry: frozenset[str],
) -> tuple[DriftEvent, ...]:
    """
    Emit ``tools_yaml_invalid`` for runbooks listing tools missing from the registry.

    Pure CPU; no DB access required. Empty ``tool_registry`` is treated as
    "registry not configured" — the sweep no-ops and emits a structured
    log so the operator knows we skipped (vs. flagging every tool as
    missing). This keeps unconfigured deployments quiet.
    """
    if not tool_registry:
        logs.log_event(
            "runbook_drift_sweep_tools_skipped",
            params={"reason": "tool_registry_empty"},
        )
        return ()
    events: list[DriftEvent] = []
    for runbook in runbooks.values():
        configured_tools = runbook.tools.allowed_tool_names
        missing = configured_tools - tool_registry
        if not missing:
            continue
        # Stable ordering inside the JSONB payload so identical drift
        # produces identical detail-hash → dedup works across cron ticks.
        ordered_missing = tuple(sorted(missing))
        detail = runbook_drift.ToolsYamlInvalidDetail(missing_tool_names=ordered_missing)
        events.append(
            DriftEvent(
                runbook_id=runbook.runbook_id,
                runbook_content_sha=runbook.metadata.content_sha,
                drift_type="tools_yaml_invalid",
                drift_severity=_SEVERITY_BY_DRIFT_TYPE["tools_yaml_invalid"],
                drift_detail=detail.model_dump(),
            )
        )
    return tuple(events)


# ---------------------------------------------------------------------------
# Aggregation helper used by the script
# ---------------------------------------------------------------------------


def aggregate_events(*event_groups: Iterable[DriftEvent]) -> tuple[DriftEvent, ...]:
    """
    Flatten the per-sweep tuples into a single ordered tuple.

    Stable order (sweep order, then per-sweep emit order) so the script's
    log output is deterministic across runs.
    """
    out: list[DriftEvent] = []
    for group in event_groups:
        out.extend(group)
    return tuple(out)


# ---------------------------------------------------------------------------
# Generic disambiguator binding for the cron's fixture-replay path
# ---------------------------------------------------------------------------


class _AlphabeticalDisambiguator:
    """
    Disambiguator that always raises ``DisambiguatorUnavailableError``.

    The cron path runs the matcher in a deterministic, no-LLM mode: ties
    fall back to alphabetical, and zero-match rescue short-circuits to
    ``no_match``. We emulate that by raising the matcher's documented
    "transport down" exception, which both Stage 2A and Stage 2B handle
    explicitly. Keeping this separate from the production disambiguator
    means cron output never depends on LLM availability.
    """

    async def __call__(
        self,
        _summary: str,
        _candidates: tuple[tuple[str, str], ...],
    ) -> models.DisambiguatorChoice:
        raise models.DisambiguatorUnavailableError("cron disambiguator: deterministic mode")


def build_deterministic_disambiguator() -> _AlphabeticalDisambiguator:
    """
    Return a disambiguator suitable for the cron's fixture-replay sweep.

    Production cron path keeps the matcher LLM-free so re-runs are
    deterministic — fixture failures must be reproducible across cron
    ticks for the dedup-by-detail-hash logic to suppress spurious
    re-pages.
    """
    return _AlphabeticalDisambiguator()


__all__ = [
    "DriftEvent",
    "ReplayMatcher",
    "aggregate_events",
    "build_deterministic_disambiguator",
    "sweep_fixture_replays",
    "sweep_stale_runbooks",
    "sweep_tools_registry",
]
