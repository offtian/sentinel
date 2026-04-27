"""
Unit tests for the three-stage runbook matcher.

Stage 1 — deterministic tag pre-filter: covers single match, no match,
deprecated skip, mnpi gate, severity filter, resource_kind filter,
exclude_labels gate, multi-tag scoring, deterministic ordering, and the
per-runbook ``min_match_score`` threshold.

Stage 2A — tie disambiguation with a fake disambiguator function: tie of 2
LLM picks one, tie of 3 LLM picks one, LLM returns no_match below
threshold (alphabetical fallback), LLM returns low confidence
(alphabetical fallback), LLM unavailable (alphabetical fallback).

Stage 2B — zero-match rescue: rescue picks one, returns no_match below
threshold, LLM unavailable returns straight no_match. End-to-end orchestrator
test confirms tag → tie → rescue routing.

Stage 3 (F6.J) — RAG / pgvector fallback: covers embedder unavailable,
zero candidates above threshold, top candidate missing from catalog
(defensive), happy-path evidence write + match. Orchestrator coverage
includes the disabled / None / Stage-2B-match / Stage-1-found short-circuits
plus the "Stage 2B no_match + RAG enabled" routing.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

import attrs
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.runbooks import matcher, models, rag


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _StubAlert:
    """Minimal MatchableAlert stub for unit testing."""

    alertname: str
    severity: str = "P2"
    resource_kind: str = "Pod"
    labels: Mapping[str, str] = dataclasses.field(default_factory=dict)
    pii_class: str = "internal"


def _make_envelope() -> envelope_mod.Envelope:
    return envelope_mod.Envelope(
        request_id=uuid4(),
        tenant_id="tenant-x",
        cluster_id="cluster-1",
        region="us-east",
        pii_class="internal",
        received_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
    )


def _make_runbook(
    *,
    runbook_id: str,
    description: str = "Procedure description.",
    alertnames: tuple[str, ...] = (),
    severity_min: str = "P3",
    resource_kinds: tuple[str, ...] = ("Pod",),
    exclude_labels: dict[str, tuple[str, ...]] | None = None,
    tags: tuple[models.RunbookTag, ...] = (),
    min_match_score: int = 2,
    deprecated_at: date | None = None,
    mnpi_safe: bool = True,
) -> models.Runbook:
    metadata = models.RunbookMetadata(
        runbook_id=runbook_id,
        description=description,
        content_sha="0" * 32,
        applies_to=models.RunbookAppliesTo(
            alertnames=alertnames,
            severity_min=severity_min,
            resource_kinds=resource_kinds,
            exclude_labels=exclude_labels or {},
        ),
        tags=tags,
        min_match_score=min_match_score,
        owner="sre-platform",
        authors=("ollie.tian",),
        last_validated=date(2026, 4, 26),
        deprecated_at=deprecated_at,
        superseded_by=None,
        mnpi_safe=mnpi_safe,
        canonical_sources=(),
    )
    return models.Runbook(
        metadata=metadata,
        body="placeholder body",
        tools=models.ToolsConfig(
            allowed_tools=(),
            denied_tools=(),
            max_total_tool_calls=10,
            max_loop_iterations=4,
        ),
        checks=models.ChecksConfig(
            prescribed_checks=(),
            groundedness_rules=(),
            body_sanitization=models.BodySanitizationConfig(
                reject_auto_rendered_urls=False,
                allowed_url_locations=(),
            ),
        ),
        tests=(),
        directory=Path("/tmp/runbooks") / runbook_id,  # noqa: S108  # synthetic dir; not opened
    )


def _catalog(*runbooks: models.Runbook) -> Mapping[str, models.Runbook]:
    return {runbook.metadata.runbook_id: runbook for runbook in runbooks}


def _scripted_disambiguator(
    *responses: models.DisambiguatorChoice,
) -> matcher.DisambiguatorFn:
    """Return a disambiguator that yields ``responses`` in order on each call."""
    queue = list(responses)

    async def _impl(
        _summary: str,
        _candidates: tuple[tuple[str, str], ...],
    ) -> models.DisambiguatorChoice:
        if not queue:
            raise AssertionError("scripted disambiguator exhausted")
        return queue.pop(0)

    return _impl


def _failing_disambiguator(message: str = "transport down") -> matcher.DisambiguatorFn:
    """Return a disambiguator that always raises DisambiguatorUnavailableError."""

    async def _impl(
        _summary: str,
        _candidates: tuple[tuple[str, str], ...],
    ) -> models.DisambiguatorChoice:
        raise models.DisambiguatorUnavailableError(message)

    return _impl


# ---------------------------------------------------------------------------
# Stage 1 — deterministic tag pre-filter
# ---------------------------------------------------------------------------


class TestStage1TagMatch:
    def test_returns_single_candidate_when_only_one_matches(self) -> None:
        # Given a catalog with one runbook that matches the alert and one that does not
        crashloop = _make_runbook(
            runbook_id="k8s-crashloop",
            alertnames=("KubePodCrashLooping",),
            tags=(models.RunbookTag(key="cluster_class", value="prod"),),
        )
        unrelated = _make_runbook(
            runbook_id="db-failover",
            alertnames=("DatabasePrimaryDown",),
            tags=(),
            resource_kinds=("Database",),
        )
        catalog = _catalog(crashloop, unrelated)
        alert = _StubAlert(
            alertname="KubePodCrashLooping",
            labels={"cluster_class": "prod"},
        )

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(alert=alert, runbooks=catalog)

        # Then the crashloop runbook is the sole candidate with score 2
        assert len(candidates) == 1
        assert candidates[0].runbook_id == "k8s-crashloop"
        assert candidates[0].score == 2
        assert candidates[0].matched_via == "exact_tag"

    def test_returns_empty_when_no_runbook_matches(self) -> None:
        # Given a catalog with one runbook for a different alertname
        only_runbook = _make_runbook(
            runbook_id="db-failover",
            alertnames=("DatabasePrimaryDown",),
            resource_kinds=("Database",),
            tags=(models.RunbookTag(key="cluster_class", value="prod"),),
        )
        alert = _StubAlert(alertname="UnknownAlert", labels={})

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(alert=alert, runbooks=_catalog(only_runbook))

        # Then no candidates are returned
        assert candidates == ()

    def test_skips_deprecated_runbooks(self) -> None:
        # Given a runbook marked deprecated that would otherwise match
        deprecated = _make_runbook(
            runbook_id="legacy",
            alertnames=("PodCrash",),
            tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            deprecated_at=date(2026, 1, 1),
        )
        alert = _StubAlert(alertname="PodCrash", labels={"cluster_class": "prod"})

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(alert=alert, runbooks=_catalog(deprecated))

        # Then the deprecated runbook is excluded
        assert candidates == ()

    def test_excludes_mnpi_unsafe_runbooks_for_mnpi_alerts(self) -> None:
        # Given a runbook that is not mnpi-safe and an alert flagged mnpi
        unsafe = _make_runbook(
            runbook_id="diagnostic",
            alertnames=("PodCrash",),
            tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            mnpi_safe=False,
        )
        mnpi_alert = _StubAlert(
            alertname="PodCrash",
            labels={"cluster_class": "prod"},
            pii_class="mnpi",
        )

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(alert=mnpi_alert, runbooks=_catalog(unsafe))

        # Then the runbook is excluded by the mnpi gate
        assert candidates == ()

    def test_drops_runbooks_below_severity_threshold(self) -> None:
        # Given a runbook requiring severity_min=P2 and a P3 alert (lower)
        runbook = _make_runbook(
            runbook_id="critical-only",
            alertnames=("PodCrash",),
            tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            severity_min="P2",
        )
        low_severity_alert = _StubAlert(
            alertname="PodCrash",
            severity="P3",
            labels={"cluster_class": "prod"},
        )

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(
            alert=low_severity_alert, runbooks=_catalog(runbook)
        )

        # Then the alert fails the severity_min check (P3 < P2)
        assert candidates == ()

    def test_filters_by_resource_kind(self) -> None:
        # Given a runbook scoped to Database resources and an alert for a Pod
        db_runbook = _make_runbook(
            runbook_id="db",
            alertnames=("ResourceAlert",),
            resource_kinds=("Database",),
            tags=(),
            min_match_score=1,
        )
        pod_alert = _StubAlert(
            alertname="ResourceAlert",
            resource_kind="Pod",
            labels={},
        )

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(alert=pod_alert, runbooks=_catalog(db_runbook))

        # Then the runbook is excluded by resource_kind mismatch
        assert candidates == ()

    def test_excludes_runbooks_when_alert_label_in_exclude_set(self) -> None:
        # Given a runbook that excludes pm_namespace="restricted"
        runbook = _make_runbook(
            runbook_id="generic",
            alertnames=("PodCrash",),
            tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            exclude_labels={"pm_namespace": ("restricted",)},
        )
        restricted_alert = _StubAlert(
            alertname="PodCrash",
            labels={"cluster_class": "prod", "pm_namespace": "restricted"},
        )

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(alert=restricted_alert, runbooks=_catalog(runbook))

        # Then the runbook is excluded
        assert candidates == ()

    def test_score_increments_per_matched_tag(self) -> None:
        # Given a runbook with three deterministic match dimensions
        runbook = _make_runbook(
            runbook_id="rich",
            alertnames=("PodCrash",),
            tags=(
                models.RunbookTag(key="cluster_class", value="prod"),
                models.RunbookTag(key="region", value="us-east"),
            ),
            min_match_score=1,
        )
        rich_alert = _StubAlert(
            alertname="PodCrash",
            labels={"cluster_class": "prod", "region": "us-east"},
        )

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(alert=rich_alert, runbooks=_catalog(runbook))

        # Then the score is 3 (alertname + cluster_class + region)
        assert candidates[0].score == 3

    def test_drops_candidates_below_per_runbook_min_match_score(self) -> None:
        # Given a runbook requiring 3 matches and an alert that matches only 1 tag
        strict = _make_runbook(
            runbook_id="strict",
            alertnames=("PodCrash",),
            tags=(
                models.RunbookTag(key="cluster_class", value="prod"),
                models.RunbookTag(key="region", value="us-east"),
            ),
            min_match_score=3,
        )
        sparse_alert = _StubAlert(
            alertname="PodCrash",
            labels={},  # no tag matches
        )

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(alert=sparse_alert, runbooks=_catalog(strict))

        # Then the runbook is excluded (score 1 < min 3)
        assert candidates == ()

    def test_orders_by_descending_score_then_alphabetical_id(self) -> None:
        # Given three runbooks with mixed scores so ordering is observable
        high_z = _make_runbook(
            runbook_id="z-high",
            alertnames=("PodCrash",),
            tags=(
                models.RunbookTag(key="cluster_class", value="prod"),
                models.RunbookTag(key="region", value="us-east"),
            ),
            min_match_score=1,
        )
        high_a = _make_runbook(
            runbook_id="a-high",
            alertnames=("PodCrash",),
            tags=(
                models.RunbookTag(key="cluster_class", value="prod"),
                models.RunbookTag(key="region", value="us-east"),
            ),
            min_match_score=1,
        )
        low_score = _make_runbook(
            runbook_id="m-low",
            alertnames=("PodCrash",),
            tags=(),
            min_match_score=1,
        )
        alert = _StubAlert(
            alertname="PodCrash",
            labels={"cluster_class": "prod", "region": "us-east"},
        )

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(
            alert=alert,
            runbooks=_catalog(high_z, high_a, low_score),
        )

        # Then high-score candidates come first (alphabetically tied), low-score last
        ordered_ids = [c.runbook_id for c in candidates]
        assert ordered_ids == ["a-high", "z-high", "m-low"]

    def test_stage_1_skips_underscore_prefixed_runbooks(self) -> None:
        # Given a catalog with two underscore-prefixed workflow-internal
        # runbooks (the F6.K _sre-base preamble and the F6.B
        # _generic-investigation fallback) plus one user-selectable runbook
        # that matches the alert; the underscore-prefixed runbooks have
        # min_match_score=0 and empty alertnames/tags so without the
        # underscore guard they would tie with the genuine match at score
        # zero and burn an LLM call (F6.K cross-cutting fix).
        sre_base = _make_runbook(
            runbook_id="_sre-base",
            alertnames=(),
            tags=(),
            resource_kinds=(),
            min_match_score=0,
        )
        generic_fallback = _make_runbook(
            runbook_id="_generic-investigation",
            alertnames=(),
            tags=(),
            resource_kinds=(),
            min_match_score=0,
        )
        crashloop = _make_runbook(
            runbook_id="k8s-crashloop",
            alertnames=("KubePodCrashLooping",),
            tags=(models.RunbookTag(key="cluster_class", value="prod"),),
        )
        catalog = _catalog(sre_base, generic_fallback, crashloop)
        alert = _StubAlert(
            alertname="KubePodCrashLooping",
            labels={"cluster_class": "prod"},
        )

        # When Stage 1 runs
        candidates = matcher.stage_1_tag_match(alert=alert, runbooks=catalog)

        # Then the underscore-prefixed runbooks are filtered out and only
        # the user-selectable crashloop runbook is returned (no spurious
        # score-zero ties on workflow internals).
        candidate_ids = [c.runbook_id for c in candidates]
        assert candidate_ids == ["k8s-crashloop"]


# ---------------------------------------------------------------------------
# Stage 2A — tie disambiguation
# ---------------------------------------------------------------------------


class TestStage2ATieDisambiguation:
    @pytest.mark.asyncio
    async def test_picks_llm_choice_when_confidence_above_threshold(self) -> None:
        # Given a tie between two candidates and an LLM that picks the second with high confidence
        runbook_a = _make_runbook(
            runbook_id="alpha",
            alertnames=("PodCrash",),
            tags=(models.RunbookTag(key="cluster_class", value="prod"),),
        )
        runbook_b = _make_runbook(
            runbook_id="bravo",
            alertnames=("PodCrash",),
            tags=(models.RunbookTag(key="cluster_class", value="prod"),),
        )
        catalog = _catalog(runbook_a, runbook_b)
        candidates = matcher.stage_1_tag_match(
            alert=_StubAlert(
                alertname="PodCrash",
                labels={"cluster_class": "prod"},
            ),
            runbooks=catalog,
        )
        assert len(candidates) == 2
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="bravo",
                justification="The bravo runbook applies because of X",
                confidence=0.9,
            )
        )

        # When Stage 2A runs
        match = await matcher.stage_2a_tie_disambiguate(
            alert=_StubAlert(
                alertname="PodCrash",
                labels={"cluster_class": "prod"},
            ),
            candidates=candidates,
            runbooks=catalog,
            disambiguator=disambiguator,
        )

        # Then the LLM-chosen runbook wins with method=llm_disambiguator_tie
        assert match.matched_runbook_id == "bravo"
        assert match.match_method == "llm_disambiguator_tie"
        assert match.confidence == 0.9
        assert match.llm_choice == "bravo"

    @pytest.mark.asyncio
    async def test_handles_three_way_tie_capped_at_top_three(self) -> None:
        # Given three tied candidates; LLM picks the middle one
        catalog = _catalog(
            _make_runbook(
                runbook_id="alpha",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
            _make_runbook(
                runbook_id="bravo",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
            _make_runbook(
                runbook_id="charlie",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
        )
        alert = _StubAlert(alertname="PodCrash", labels={"cluster_class": "prod"})
        candidates = matcher.stage_1_tag_match(alert=alert, runbooks=catalog)
        assert len(candidates) == 3
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="bravo",
                justification="best fit",
                confidence=0.7,
            )
        )

        # When Stage 2A runs
        match = await matcher.stage_2a_tie_disambiguate(
            alert=alert,
            candidates=candidates,
            runbooks=catalog,
            disambiguator=disambiguator,
        )

        # Then the middle candidate wins
        assert match.matched_runbook_id == "bravo"
        assert match.match_method == "llm_disambiguator_tie"

    @pytest.mark.asyncio
    async def test_falls_back_to_alphabetical_when_llm_returns_no_match(self) -> None:
        # Given a tie and an LLM that returns no_match
        catalog = _catalog(
            _make_runbook(
                runbook_id="zulu",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
            _make_runbook(
                runbook_id="alpha",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
        )
        alert = _StubAlert(alertname="PodCrash", labels={"cluster_class": "prod"})
        candidates = matcher.stage_1_tag_match(alert=alert, runbooks=catalog)
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="no_match",
                justification="no fit",
                confidence=0.95,
            )
        )

        # When Stage 2A runs
        match = await matcher.stage_2a_tie_disambiguate(
            alert=alert,
            candidates=candidates,
            runbooks=catalog,
            disambiguator=disambiguator,
        )

        # Then alphabetical winner is selected with method=alphabetical_fallback
        assert match.matched_runbook_id == "alpha"
        assert match.match_method == "alphabetical_fallback"
        assert match.llm_choice == "no_match"

    @pytest.mark.asyncio
    async def test_falls_back_to_alphabetical_when_llm_low_confidence(self) -> None:
        # Given a tie and an LLM that picks one with confidence below 0.5
        catalog = _catalog(
            _make_runbook(
                runbook_id="zulu",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
            _make_runbook(
                runbook_id="alpha",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
        )
        alert = _StubAlert(alertname="PodCrash", labels={"cluster_class": "prod"})
        candidates = matcher.stage_1_tag_match(alert=alert, runbooks=catalog)
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="zulu",
                justification="maybe",
                confidence=0.3,
            )
        )

        # When Stage 2A runs
        match = await matcher.stage_2a_tie_disambiguate(
            alert=alert,
            candidates=candidates,
            runbooks=catalog,
            disambiguator=disambiguator,
        )

        # Then alphabetical fallback fires (alpha wins) but LLM choice is recorded
        assert match.matched_runbook_id == "alpha"
        assert match.match_method == "alphabetical_fallback"
        assert match.llm_choice == "zulu"
        assert match.confidence == 0.3

    @pytest.mark.asyncio
    async def test_uses_alphabetical_when_disambiguator_unavailable(self) -> None:
        # Given a tie and a disambiguator that fails with DisambiguatorUnavailableError
        catalog = _catalog(
            _make_runbook(
                runbook_id="zebra",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
            _make_runbook(
                runbook_id="aardvark",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
        )
        alert = _StubAlert(alertname="PodCrash", labels={"cluster_class": "prod"})
        candidates = matcher.stage_1_tag_match(alert=alert, runbooks=catalog)
        disambiguator = _failing_disambiguator()

        # When Stage 2A runs
        match = await matcher.stage_2a_tie_disambiguate(
            alert=alert,
            candidates=candidates,
            runbooks=catalog,
            disambiguator=disambiguator,
        )

        # Then alphabetical_fallback fires and llm_choice stays None
        assert match.matched_runbook_id == "aardvark"
        assert match.match_method == "alphabetical_fallback"
        assert match.llm_choice is None
        assert match.confidence == 0.0


# ---------------------------------------------------------------------------
# Stage 2B — zero-match rescue
# ---------------------------------------------------------------------------


class TestStage2BZeroMatchRescue:
    @pytest.mark.asyncio
    async def test_picks_llm_choice_when_confidence_above_threshold(self) -> None:
        # Given an alert with no Stage-1 match and an LLM that picks one rescue candidate
        catalog = _catalog(
            _make_runbook(
                runbook_id="generic-investigation",
                alertnames=(),
                tags=(),
                resource_kinds=(),
            ),
            _make_runbook(
                runbook_id="db-failover",
                alertnames=("DatabasePrimaryDown",),
                resource_kinds=("Database",),
                tags=(),
            ),
        )
        novel_alert = _StubAlert(
            alertname="UnknownService_500s",
            resource_kind="Pod",
            labels={},
        )
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="generic-investigation",
                justification="best generic fit",
                confidence=0.8,
            )
        )

        # When Stage 2B runs
        match = await matcher.stage_2b_zero_match_rescue(
            alert=novel_alert, runbooks=catalog, disambiguator=disambiguator
        )

        # Then the rescued runbook wins with method=llm_zero_match_rescue
        assert match.matched_runbook_id == "generic-investigation"
        assert match.match_method == "llm_zero_match_rescue"
        assert match.confidence == 0.8

    @pytest.mark.asyncio
    async def test_returns_no_match_when_llm_below_zero_match_threshold(self) -> None:
        # Given a novel alert and an LLM that picks a rescue candidate with confidence 0.55 (< 0.6)
        catalog = _catalog(
            _make_runbook(
                runbook_id="generic-investigation",
                alertnames=(),
                tags=(),
                resource_kinds=(),
            ),
        )
        alert = _StubAlert(alertname="WeirdNewAlert", labels={})
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="generic-investigation",
                justification="maybe",
                confidence=0.55,
            )
        )

        # When Stage 2B runs
        match = await matcher.stage_2b_zero_match_rescue(
            alert=alert, runbooks=catalog, disambiguator=disambiguator
        )

        # Then no_match is returned but LLM justification is recorded for audit
        assert match.matched_runbook_id is None
        assert match.match_method == "no_match"
        assert match.confidence == 0.55
        assert match.llm_justification == "maybe"

    @pytest.mark.asyncio
    async def test_returns_no_match_when_llm_explicitly_says_no_match(self) -> None:
        # Given a novel alert and an LLM that explicitly returns no_match
        catalog = _catalog(
            _make_runbook(
                runbook_id="generic-investigation",
                alertnames=(),
                tags=(),
                resource_kinds=(),
            ),
        )
        alert = _StubAlert(alertname="UnknownAlert", labels={})
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="no_match",
                justification="genuinely novel",
                confidence=0.9,
            )
        )

        # When Stage 2B runs
        match = await matcher.stage_2b_zero_match_rescue(
            alert=alert, runbooks=catalog, disambiguator=disambiguator
        )

        # Then no_match is returned with the LLM choice recorded
        assert match.matched_runbook_id is None
        assert match.match_method == "no_match"
        assert match.llm_choice == "no_match"

    @pytest.mark.asyncio
    async def test_returns_straight_no_match_when_disambiguator_unavailable(
        self,
    ) -> None:
        # Given a novel alert and a disambiguator that raises
        catalog = _catalog(
            _make_runbook(
                runbook_id="generic-investigation",
                alertnames=(),
                tags=(),
                resource_kinds=(),
            ),
        )
        alert = _StubAlert(alertname="UnknownAlert", labels={})
        disambiguator = _failing_disambiguator()

        # When Stage 2B runs
        match = await matcher.stage_2b_zero_match_rescue(
            alert=alert, runbooks=catalog, disambiguator=disambiguator
        )

        # Then no rescue is attempted and method=no_match (no alphabetical fallback at this stage)
        assert match.matched_runbook_id is None
        assert match.match_method == "no_match"
        assert match.llm_choice is None
        # Candidates still populated with eligible top-N for audit
        assert any(c.runbook_id == "generic-investigation" for c in match.candidates)

    @pytest.mark.asyncio
    async def test_returns_no_match_when_no_eligible_candidates(self) -> None:
        # Given a catalog where every runbook is filtered out by severity / resource_kind
        catalog = _catalog(
            _make_runbook(
                runbook_id="critical-only",
                alertnames=("PodCrash",),
                resource_kinds=("Pod",),
                severity_min="P1",
            ),
        )
        # P3 alert is below the P1 severity_min so the runbook is ineligible
        alert = _StubAlert(alertname="UnknownAlert", severity="P3", labels={})

        # When Stage 2B runs
        match = await matcher.stage_2b_zero_match_rescue(
            alert=alert,
            runbooks=catalog,
            disambiguator=_scripted_disambiguator(),
        )

        # Then no_match is returned with empty candidates (and the LLM was never invoked)
        assert match.matched_runbook_id is None
        assert match.match_method == "no_match"
        assert match.candidates == ()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class TestMatchRunbookOrchestrator:
    @pytest.mark.asyncio
    async def test_returns_tag_match_when_unambiguous(self) -> None:
        # Given a single Stage-1 match
        catalog = _catalog(
            _make_runbook(
                runbook_id="k8s-crashloop",
                alertnames=("KubePodCrashLooping",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
        )
        alert = _StubAlert(
            alertname="KubePodCrashLooping",
            labels={"cluster_class": "prod"},
        )

        # When the orchestrator runs
        match = await matcher.match_runbook(
            alert=alert,
            envelope=_make_envelope(),
            runbooks=catalog,
            disambiguator=_scripted_disambiguator(),
        )

        # Then method=tag with confidence 1.0 (no LLM consulted)
        assert match.matched_runbook_id == "k8s-crashloop"
        assert match.match_method == "tag"
        assert match.confidence == 1.0

    @pytest.mark.asyncio
    async def test_routes_tied_matches_through_stage_2a(self) -> None:
        # Given a tie at the top score with the LLM picking the second
        catalog = _catalog(
            _make_runbook(
                runbook_id="alpha",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
            _make_runbook(
                runbook_id="bravo",
                alertnames=("PodCrash",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
        )
        alert = _StubAlert(alertname="PodCrash", labels={"cluster_class": "prod"})
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="bravo",
                justification="bravo is the right one",
                confidence=0.85,
            )
        )

        # When the orchestrator runs
        match = await matcher.match_runbook(
            alert=alert,
            envelope=_make_envelope(),
            runbooks=catalog,
            disambiguator=disambiguator,
        )

        # Then Stage 2A is invoked and the LLM choice wins
        assert match.matched_runbook_id == "bravo"
        assert match.match_method == "llm_disambiguator_tie"

    @pytest.mark.asyncio
    async def test_routes_zero_matches_through_stage_2b(self) -> None:
        # Given no Stage-1 matches and an LLM that rescues with high confidence
        catalog = _catalog(
            _make_runbook(
                runbook_id="generic-investigation",
                alertnames=(),
                tags=(),
                resource_kinds=(),
            ),
        )
        alert = _StubAlert(alertname="UnknownAlert", labels={})
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="generic-investigation",
                justification="generic exploration applies",
                confidence=0.75,
            )
        )

        # When the orchestrator runs
        match = await matcher.match_runbook(
            alert=alert,
            envelope=_make_envelope(),
            runbooks=catalog,
            disambiguator=disambiguator,
        )

        # Then Stage 2B is invoked and the LLM-rescued runbook wins
        assert match.matched_runbook_id == "generic-investigation"
        assert match.match_method == "llm_zero_match_rescue"


# ---------------------------------------------------------------------------
# Stage 3 — RAG fallback (F6.J)
# ---------------------------------------------------------------------------


@attrs.frozen(kw_only=True, slots=True)
class _StubEmbedder:
    """Minimal :class:`rag.Embedder` test double for Stage 3 unit tests."""

    model_id: str = "stub/embed-test"
    model_version: str = "v1"

    async def embed(self, text: str) -> tuple[float, ...]:
        # Return a fixed-shape vector — exact values are irrelevant because
        # ``rag.retrieve_top_k`` and ``rag.write_evidence_rows`` are mocked.
        return (0.0,) * 4


def _make_rag_fallback(
    *,
    enabled: bool = True,
    top_k: int = 5,
    min_similarity: float = 0.78,
) -> rag.RagFallback:
    """Return a :class:`rag.RagFallback` wired with mocks for Stage 3 tests."""
    return rag.RagFallback(
        embedder=_StubEmbedder(),
        session=mock.AsyncMock(spec=AsyncSession),
        enabled=enabled,
        top_k=top_k,
        min_similarity=min_similarity,
    )


def _make_rag_candidate(
    *,
    runbook_id: str,
    cosine_similarity: float = 0.92,
    rank: int = 1,
    embedding_section: str = "description",
) -> rag.RunbookRagCandidate:
    """Return a deterministic :class:`rag.RunbookRagCandidate` for assertions."""
    return rag.RunbookRagCandidate(
        runbook_id=runbook_id,
        content_sha="0" * 32,
        embedding_section=embedding_section,
        cosine_similarity=cosine_similarity,
        rank=rank,
    )


class TestStage3RagFallback:
    @pytest.mark.asyncio
    async def test_returns_no_match_when_embedder_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a RAG fallback whose retrieve_top_k raises EmbedderUnavailableError
        catalog = _catalog(_make_runbook(runbook_id="k8s-crashloop"))
        alert = _StubAlert(alertname="UnknownAlert", labels={})
        rag_fallback = _make_rag_fallback()

        async def _raises(**_kwargs: object) -> tuple[rag.RunbookRagCandidate, ...]:
            raise rag.EmbedderUnavailableError("transport down")

        monkeypatch.setattr(matcher.rag, "retrieve_top_k", _raises)
        evidence_mock = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(matcher.rag, "write_evidence_rows", evidence_mock)

        # When Stage 3 runs
        match = await matcher.stage_3_rag_fallback(
            alert=alert, runbooks=catalog, rag_fallback=rag_fallback
        )

        # Then a no_match is returned, no evidence rows are written, and the
        # last_candidates side-channel is cleared
        assert match.match_method == "no_match"
        assert match.matched_runbook_id is None
        assert rag_fallback.last_candidates == ()
        assert rag_fallback.last_match_id is None
        evidence_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_no_match_when_zero_candidates_above_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given retrieve_top_k returns no candidates (all below min_similarity)
        catalog = _catalog(_make_runbook(runbook_id="k8s-crashloop"))
        alert = _StubAlert(alertname="UnknownAlert", labels={})
        rag_fallback = _make_rag_fallback()

        retrieve_mock = mock.AsyncMock(return_value=())
        monkeypatch.setattr(matcher.rag, "retrieve_top_k", retrieve_mock)
        evidence_mock = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(matcher.rag, "write_evidence_rows", evidence_mock)

        # When Stage 3 runs
        match = await matcher.stage_3_rag_fallback(
            alert=alert, runbooks=catalog, rag_fallback=rag_fallback
        )

        # Then no_match is returned, evidence rows are NOT written, and the
        # last_candidates side-channel is cleared
        assert match.match_method == "no_match"
        assert match.matched_runbook_id is None
        assert rag_fallback.last_candidates == ()
        evidence_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_no_match_when_top_candidate_missing_from_catalog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given retrieve_top_k returns a candidate whose runbook_id has been
        # removed from the catalog between indexing and retrieval (defensive)
        catalog = _catalog(_make_runbook(runbook_id="k8s-crashloop"))
        alert = _StubAlert(alertname="UnknownAlert", labels={})
        rag_fallback = _make_rag_fallback()
        ghost_candidate = _make_rag_candidate(runbook_id="deleted-runbook")

        retrieve_mock = mock.AsyncMock(return_value=(ghost_candidate,))
        monkeypatch.setattr(matcher.rag, "retrieve_top_k", retrieve_mock)
        evidence_mock = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(matcher.rag, "write_evidence_rows", evidence_mock)

        # When Stage 3 runs
        match = await matcher.stage_3_rag_fallback(
            alert=alert, runbooks=catalog, rag_fallback=rag_fallback
        )

        # Then no_match is returned without writing evidence — runbook may
        # have been deleted between indexing + retrieval
        assert match.match_method == "no_match"
        assert match.matched_runbook_id is None
        assert rag_fallback.last_candidates == ()
        evidence_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_writes_evidence_rows_and_returns_match_on_hit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given retrieve_top_k returns a candidate present in the catalog
        crashloop = _make_runbook(runbook_id="k8s-crashloop")
        catalog = _catalog(crashloop)
        alert = _StubAlert(alertname="NovelAlert", labels={})
        rag_fallback = _make_rag_fallback()
        top_candidate = _make_rag_candidate(
            runbook_id="k8s-crashloop", cosine_similarity=0.91, rank=1
        )
        runner_up = _make_rag_candidate(
            runbook_id="k8s-crashloop",
            cosine_similarity=0.85,
            rank=2,
            embedding_section="body",
        )
        rag_candidates = (top_candidate, runner_up)

        retrieve_mock = mock.AsyncMock(return_value=rag_candidates)
        monkeypatch.setattr(matcher.rag, "retrieve_top_k", retrieve_mock)
        evidence_mock = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(matcher.rag, "write_evidence_rows", evidence_mock)

        # When Stage 3 runs
        match = await matcher.stage_3_rag_fallback(
            alert=alert, runbooks=catalog, rag_fallback=rag_fallback
        )

        # Then a rag match is returned with confidence == top similarity, and
        # evidence rows are persisted under a fresh match_id surfaced on the
        # rag_fallback side-channel for the persistence layer to reuse
        assert match.matched_runbook_id == "k8s-crashloop"
        assert match.match_method == "rag"
        assert match.confidence == 0.91
        assert match.tag_score is None
        assert match.llm_choice is None
        assert match.llm_justification is None
        assert isinstance(rag_fallback.last_match_id, UUID)
        assert rag_fallback.last_candidates == rag_candidates
        evidence_mock.assert_awaited_once()
        evidence_kwargs = evidence_mock.await_args.kwargs
        assert evidence_kwargs["match_id"] == rag_fallback.last_match_id
        assert evidence_kwargs["candidates"] == rag_candidates
        assert evidence_kwargs["session"] is rag_fallback.session
        assert evidence_kwargs["embedder"] is rag_fallback.embedder


# ---------------------------------------------------------------------------
# Orchestrator — Stage 3 routing
# ---------------------------------------------------------------------------


def _no_match_disambiguator() -> matcher.DisambiguatorFn:
    """Return a disambiguator that always responds with no_match (drives Stage 2B no_match)."""

    async def _impl(
        _summary: str,
        _candidates: tuple[tuple[str, str], ...],
    ) -> models.DisambiguatorChoice:
        return models.DisambiguatorChoice(
            chosen_runbook_id="no_match",
            justification="genuinely novel",
            confidence=1.0,
        )

    return _impl


class TestMatchRunbookStage3Orchestration:
    @pytest.mark.asyncio
    async def test_skips_stage_3_when_rag_fallback_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given Stage 1 returns nothing, Stage 2B returns no_match, and the
        # rag_fallback is wired but disabled
        catalog = _catalog(
            _make_runbook(
                runbook_id="generic-investigation",
                alertnames=(),
                tags=(),
                resource_kinds=(),
            ),
        )
        alert = _StubAlert(alertname="NovelAlert", labels={})
        rag_fallback = _make_rag_fallback(enabled=False)
        retrieve_mock = mock.AsyncMock(return_value=())
        monkeypatch.setattr(matcher.rag, "retrieve_top_k", retrieve_mock)

        # When the orchestrator runs
        match = await matcher.match_runbook(
            alert=alert,
            envelope=_make_envelope(),
            runbooks=catalog,
            disambiguator=_no_match_disambiguator(),
            rag_fallback=rag_fallback,
        )

        # Then Stage 2B's no_match is returned without invoking Stage 3
        assert match.match_method == "no_match"
        retrieve_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_stage_3_when_rag_fallback_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a Stage 2B no_match path with rag_fallback=None (legacy callers)
        catalog = _catalog(
            _make_runbook(
                runbook_id="generic-investigation",
                alertnames=(),
                tags=(),
                resource_kinds=(),
            ),
        )
        alert = _StubAlert(alertname="NovelAlert", labels={})
        retrieve_mock = mock.AsyncMock(return_value=())
        monkeypatch.setattr(matcher.rag, "retrieve_top_k", retrieve_mock)

        # When the orchestrator runs without a rag_fallback
        match = await matcher.match_runbook(
            alert=alert,
            envelope=_make_envelope(),
            runbooks=catalog,
            disambiguator=_no_match_disambiguator(),
            rag_fallback=None,
        )

        # Then a straight no_match is returned without touching the RAG path
        assert match.match_method == "no_match"
        retrieve_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_stage_3_when_stage_2b_returns_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given Stage 2B rescues with a high-confidence match before Stage 3 can run
        catalog = _catalog(
            _make_runbook(
                runbook_id="generic-investigation",
                alertnames=(),
                tags=(),
                resource_kinds=(),
            ),
        )
        alert = _StubAlert(alertname="NovelAlert", labels={})
        rag_fallback = _make_rag_fallback()
        retrieve_mock = mock.AsyncMock(return_value=())
        monkeypatch.setattr(matcher.rag, "retrieve_top_k", retrieve_mock)
        disambiguator = _scripted_disambiguator(
            models.DisambiguatorChoice(
                chosen_runbook_id="generic-investigation",
                justification="generic exploration applies",
                confidence=0.8,
            )
        )

        # When the orchestrator runs
        match = await matcher.match_runbook(
            alert=alert,
            envelope=_make_envelope(),
            runbooks=catalog,
            disambiguator=disambiguator,
            rag_fallback=rag_fallback,
        )

        # Then the Stage 2B rescue match wins and Stage 3 is never invoked
        assert match.matched_runbook_id == "generic-investigation"
        assert match.match_method == "llm_zero_match_rescue"
        retrieve_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invokes_stage_3_when_stage_2b_no_match_and_rag_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given Stage 1 finds nothing, Stage 2B returns no_match, and Stage 3
        # retrieves a confident candidate
        crashloop = _make_runbook(runbook_id="k8s-crashloop")
        catalog = _catalog(crashloop)
        alert = _StubAlert(alertname="NovelAlert", labels={})
        rag_fallback = _make_rag_fallback()
        top_candidate = _make_rag_candidate(runbook_id="k8s-crashloop", cosine_similarity=0.88)
        retrieve_mock = mock.AsyncMock(return_value=(top_candidate,))
        monkeypatch.setattr(matcher.rag, "retrieve_top_k", retrieve_mock)
        evidence_mock = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(matcher.rag, "write_evidence_rows", evidence_mock)

        # When the orchestrator runs
        match = await matcher.match_runbook(
            alert=alert,
            envelope=_make_envelope(),
            runbooks=catalog,
            disambiguator=_no_match_disambiguator(),
            rag_fallback=rag_fallback,
        )

        # Then Stage 3's RAG match is returned (method=rag, confidence==similarity)
        assert match.matched_runbook_id == "k8s-crashloop"
        assert match.match_method == "rag"
        assert match.confidence == 0.88
        retrieve_mock.assert_awaited_once()
        evidence_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_stage_3_when_stage_1_finds_candidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given Stage 1 finds a single unambiguous winner (so neither Stage
        # 2B nor Stage 3 should run)
        catalog = _catalog(
            _make_runbook(
                runbook_id="k8s-crashloop",
                alertnames=("KubePodCrashLooping",),
                tags=(models.RunbookTag(key="cluster_class", value="prod"),),
            ),
        )
        alert = _StubAlert(
            alertname="KubePodCrashLooping",
            labels={"cluster_class": "prod"},
        )
        rag_fallback = _make_rag_fallback()
        retrieve_mock = mock.AsyncMock(return_value=())
        monkeypatch.setattr(matcher.rag, "retrieve_top_k", retrieve_mock)

        # When the orchestrator runs
        match = await matcher.match_runbook(
            alert=alert,
            envelope=_make_envelope(),
            runbooks=catalog,
            disambiguator=_scripted_disambiguator(),
            rag_fallback=rag_fallback,
        )

        # Then Stage 1's tag match wins and Stage 3 is never invoked (Stage
        # 3 only runs after Stage 2B no_match — never on a Stage 1 hit)
        assert match.matched_runbook_id == "k8s-crashloop"
        assert match.match_method == "tag"
        retrieve_mock.assert_not_awaited()
