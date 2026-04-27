"""
Unit tests for ``scripts/runbook_gap_flywheel.py`` and the F6.M flywheel domain.

Exercises:

* :func:`sentinel.domain.runbooks.flywheel.compute_fingerprint` determinism;
* :func:`cluster_no_match_members` grouping (3 same -> 1 cluster of 3, 2 same
  -> below-threshold cluster of 2, distinct fingerprints stay distinct);
* :func:`upsert_cluster` insert vs update semantics against a mocked
  :class:`AsyncSession` (iteration counter increments, request-id list merges);
* the script's ``_process_cluster`` threshold gate + idempotency
  (``draft_pr_url`` already populated -> no second PR opened);
* the autogen Jinja template renders + the rendered quartet round-trips
  through :func:`sentinel.domain.runbooks.loader.load_runbook` cleanly.
  This is the fail-closed gate on the auto-PR path: if the template breaks,
  the auto-PR pipeline silently puts garbage in front of reviewers.
* :func:`_open_draft_pr` shells out via injected runners (``--draft`` flag
  asserted; branch-naming format ``flywheel/runbook-gap-<fp>``).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

import attrs
import pytest

from sentinel.data.sql import runbook_gap_cluster as gap_sql
from sentinel.domain.runbooks import flywheel as flywheel_mod
from sentinel.domain.runbooks import loader as runbook_loader


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "runbook_gap_flywheel.py"
_MODULE_NAME = "sentinel_test_scripts.runbook_gap_flywheel"


def _load_flywheel_script() -> ModuleType:
    """
    Load ``scripts/runbook_gap_flywheel.py`` as a module for direct unit testing.

    The script lives outside ``src/`` because it is a CI / cron entry point.
    Loading via :mod:`importlib.util` keeps the test independent of any
    sys.path mutation while still exercising the script's real top-level
    imports.
    """
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        msg = f"could not load script module from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_FLYWHEEL_SCRIPT = _load_flywheel_script()


@attrs.frozen(kw_only=True, slots=True)
class _FakeMemberSpec:
    """
    Test-only spec for building a :class:`flywheel_mod.GapMember`.

    ``attrs.frozen`` so the test cases are themselves immutable -- mutating
    a spec to share between tests is a smell that becomes a bug as the
    cluster shapes evolve.
    """

    alertname: str
    service: str
    classification_category: str
    summary: str
    matched_at: datetime


def _make_gap_member(spec: _FakeMemberSpec) -> flywheel_mod.GapMember:
    """Return a :class:`GapMember` for the given spec, sorted-keys JSON included."""
    return flywheel_mod.GapMember(
        request_id=uuid.uuid4(),
        classification_category=spec.classification_category,
        alertname=spec.alertname,
        service=spec.service,
        labels_sorted_json=(f'{{"alertname":"{spec.alertname}","service":"{spec.service}"}}'),
        summary=spec.summary,
        matched_at=spec.matched_at,
    )


def _make_cluster(
    *,
    fingerprint: str = "deadbeefdeadbeef",
    member_count: int = 3,
    members: tuple[flywheel_mod.GapMember, ...] | None = None,
) -> flywheel_mod.GapCluster:
    """Return a :class:`GapCluster` synthesised for the script-level tests."""
    if members is None:
        spec = _FakeMemberSpec(
            alertname="PodCrashLoop",
            service="checkout-api",
            classification_category="kubernetes_pod",
            summary="checkout-api pod crash-looping after rollout",
            matched_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        )
        members = tuple(_make_gap_member(spec) for _ in range(member_count))
    return flywheel_mod.GapCluster(
        fingerprint=fingerprint,
        classification_category="kubernetes_pod",
        members=members,
        distinct_services=frozenset({"checkout-api"}),
        distinct_alertnames=frozenset({"PodCrashLoop"}),
        first_seen_at=members[0].matched_at,
        last_seen_at=members[-1].matched_at,
        representative_summary="checkout-api pod crash-looping after rollout",
    )


# ---------------------------------------------------------------------------
# Pure helpers: fingerprint + cluster grouping
# ---------------------------------------------------------------------------


class TestComputeFingerprint:
    def test_returns_16_hex_chars_deterministically(self) -> None:
        # Given the same labels JSON and category called twice
        first = flywheel_mod.compute_fingerprint(
            sorted_labels_json='{"alertname":"X","service":"Y"}',
            classification_category="kubernetes_pod",
        )
        second = flywheel_mod.compute_fingerprint(
            sorted_labels_json='{"alertname":"X","service":"Y"}',
            classification_category="kubernetes_pod",
        )

        # When the result is examined
        # Then both calls return the same 16-char hex digest
        assert first == second
        assert len(first) == 16
        assert all(ch in "0123456789abcdef" for ch in first)

    def test_different_categories_produce_different_fingerprints(self) -> None:
        # Given the same labels JSON but two different classification categories
        first = flywheel_mod.compute_fingerprint(
            sorted_labels_json='{"alertname":"X"}',
            classification_category="kubernetes_pod",
        )
        second = flywheel_mod.compute_fingerprint(
            sorted_labels_json='{"alertname":"X"}',
            classification_category="network",
        )

        # When the digests are compared
        # Then they differ -- the category is part of the hash input
        assert first != second


class TestClusterNoMatchMembers:
    def test_three_same_fingerprint_rows_form_one_cluster_of_three(self) -> None:
        # Given three GapMembers with identical labels + category
        spec = _FakeMemberSpec(
            alertname="PodCrashLoop",
            service="checkout-api",
            classification_category="kubernetes_pod",
            summary="checkout-api pod crash-looping",
            matched_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        )
        members = [_make_gap_member(spec) for _ in range(3)]

        # When clustered
        clusters = flywheel_mod.cluster_no_match_members(members)

        # Then exactly one cluster surfaces with member_count=3
        assert len(clusters) == 1
        assert clusters[0].member_count == 3
        assert clusters[0].distinct_services == frozenset({"checkout-api"})
        assert clusters[0].distinct_alertnames == frozenset({"PodCrashLoop"})

    def test_two_same_fingerprint_rows_form_a_below_threshold_cluster(self) -> None:
        # Given two GapMembers with identical fingerprint inputs
        spec = _FakeMemberSpec(
            alertname="PodCrashLoop",
            service="checkout-api",
            classification_category="kubernetes_pod",
            summary="checkout-api pod crash-looping",
            matched_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        )
        members = [_make_gap_member(spec) for _ in range(2)]

        # When clustered
        clusters = flywheel_mod.cluster_no_match_members(members)

        # Then exactly one cluster of size 2 surfaces (below the default
        # threshold of 3 -- the script's _process_cluster gate is what
        # actually suppresses the PR; the clusterer always emits the row)
        assert len(clusters) == 1
        assert clusters[0].member_count == 2

    def test_distinct_fingerprints_do_not_merge(self) -> None:
        # Given two GapMembers from distinct alerts (different alertnames)
        crash_spec = _FakeMemberSpec(
            alertname="PodCrashLoop",
            service="checkout-api",
            classification_category="kubernetes_pod",
            summary="crash-looping",
            matched_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        )
        oom_spec = _FakeMemberSpec(
            alertname="PodOOMKilled",
            service="checkout-api",
            classification_category="kubernetes_pod",
            summary="oom-killed",
            matched_at=datetime(2026, 4, 1, 13, 0, tzinfo=UTC),
        )
        members = [_make_gap_member(crash_spec), _make_gap_member(oom_spec)]

        # When clustered
        clusters = flywheel_mod.cluster_no_match_members(members)

        # Then two distinct clusters surface (one per fingerprint)
        assert len(clusters) == 2
        fingerprints = {cluster.fingerprint for cluster in clusters}
        assert len(fingerprints) == 2


# ---------------------------------------------------------------------------
# upsert_cluster against a mocked AsyncSession
# ---------------------------------------------------------------------------


def _make_session_returning(existing: Any) -> mock.AsyncMock:
    """Return an AsyncMock session whose ``execute`` resolves to ``existing``."""
    session = mock.AsyncMock()
    # session.add is synchronous on the real AsyncSession — replacing the
    # AsyncMock default avoids "coroutine was never awaited" warnings when
    # production code calls session.add(record) without await.
    session.add = mock.MagicMock()
    result = mock.MagicMock()
    result.scalar_one_or_none.return_value = existing
    session.execute.return_value = result
    return session


class TestUpsertCluster:
    @pytest.mark.asyncio
    async def test_inserts_a_fresh_record_at_iteration_one(self) -> None:
        # Given a session whose lookup finds no existing cluster row
        session = _make_session_returning(existing=None)
        cluster = _make_cluster(fingerprint="ababababcdcdcdcd", member_count=3)

        # When upsert_cluster runs
        record = await flywheel_mod.upsert_cluster(session=session, cluster=cluster)

        # Then a fresh record is added with iteration=1 and member_count=3
        session.add.assert_called_once()
        session.flush.assert_awaited()
        assert record.flywheel_iteration == 1
        assert record.member_count == 3
        assert record.fingerprint == "ababababcdcdcdcd"

    @pytest.mark.asyncio
    async def test_updates_existing_record_and_increments_iteration(self) -> None:
        # Given a session whose lookup returns a record from iteration 1 with 3 members
        existing = gap_sql.RunbookGapClusterRecord(
            fingerprint="ababababcdcdcdcd",
            classification_category="kubernetes_pod",
            representative_alert_summary="prior summary",
            member_request_ids=["00000000-0000-0000-0000-000000000001"],
            member_count=3,
            distinct_services=["checkout-api"],
            distinct_alertnames=["PodCrashLoop"],
            first_seen_at=datetime(2026, 3, 25, tzinfo=UTC),
            last_seen_at=datetime(2026, 3, 25, tzinfo=UTC),
            flywheel_iteration=1,
        )
        session = _make_session_returning(existing=existing)
        # Same fingerprint cluster carrying 2 new members in this iteration
        cluster = _make_cluster(fingerprint="ababababcdcdcdcd", member_count=2)

        # When upsert_cluster runs
        record = await flywheel_mod.upsert_cluster(session=session, cluster=cluster)

        # Then the existing record's iteration ticks to 2 and member_count grows
        assert record is existing
        assert record.flywheel_iteration == 2
        assert record.member_count == 5  # 3 existing + 2 incoming
        session.flush.assert_awaited()


# ---------------------------------------------------------------------------
# Script-level _process_cluster: threshold gate + idempotency
# ---------------------------------------------------------------------------


def _make_flywheel_config(*, min_cluster_size: int = 3) -> Any:
    """Return a :class:`FlywheelConfig` for use in script-level tests."""
    return _FLYWHEEL_SCRIPT.FlywheelConfig(
        lookback_days=7,
        min_cluster_size=min_cluster_size,
        pr_template_team="sre-platform",
    )


class TestProcessCluster:
    @pytest.mark.asyncio
    async def test_skips_below_threshold_clusters_without_upsert(self) -> None:
        # Given a cluster of size 2 (below default min_cluster_size of 3)
        cluster = _make_cluster(member_count=2)
        session = mock.AsyncMock()
        config = _make_flywheel_config()

        # When _process_cluster runs
        with mock.patch.object(flywheel_mod, "upsert_cluster", new=mock.AsyncMock()) as upsert_spy:
            await _FLYWHEEL_SCRIPT._process_cluster(
                session=session,
                cluster=cluster,
                config=config,
                today=date(2026, 4, 1),
                dry_run=False,
                subprocess_runner=None,
                git_runner=None,
            )

        # Then the cluster is never even upserted (the threshold gate
        # short-circuits before any DB write -- below-threshold clusters
        # surface only on a future iteration when they cross the line)
        upsert_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotent_when_draft_pr_url_already_populated(self) -> None:
        # Given a qualifying cluster but the persisted record already carries
        # a draft_pr_url from a prior run
        cluster = _make_cluster(member_count=3)
        config = _make_flywheel_config()
        existing_record = mock.MagicMock(spec=gap_sql.RunbookGapClusterRecord)
        existing_record.member_count = 6  # 3 existing + 3 new
        existing_record.draft_pr_url = "https://github.com/acme/sentinel/pull/42"
        existing_record.flywheel_iteration = 2

        session = mock.AsyncMock()
        sentinel_runner = mock.AsyncMock()

        # When _process_cluster runs
        with (
            mock.patch.object(
                flywheel_mod, "upsert_cluster", new=mock.AsyncMock(return_value=existing_record)
            ),
            mock.patch.object(
                _FLYWHEEL_SCRIPT, "_open_draft_pr", new=mock.AsyncMock()
            ) as open_pr_spy,
        ):
            await _FLYWHEEL_SCRIPT._process_cluster(
                session=session,
                cluster=cluster,
                config=config,
                today=date(2026, 4, 1),
                dry_run=False,
                subprocess_runner=sentinel_runner,
                git_runner=None,
            )

        # Then no second PR is opened (idempotent on draft_pr_url)
        open_pr_spy.assert_not_called()


# ---------------------------------------------------------------------------
# Template -> on-disk quartet -> loader round-trip (fail-closed gate)
# ---------------------------------------------------------------------------


class TestRenderRunbookSkeletonLoadsCleanly:
    def test_rendered_quartet_loads_via_runbook_loader(self, tmp_path: Path) -> None:
        # Given a synthetic cluster, the autogen Jinja template, and the
        # script's stub renderers for the three sidecars
        cluster = _make_cluster(fingerprint="abcdef0123456789", member_count=3)
        runbook_md = _FLYWHEEL_SCRIPT.render_runbook_skeleton(
            cluster=cluster,
            today=date(2026, 4, 27),
        )
        tools_yaml = _FLYWHEEL_SCRIPT.render_stub_tools_yaml()
        checks_yaml = _FLYWHEEL_SCRIPT.render_stub_checks_yaml()
        tests_yaml = _FLYWHEEL_SCRIPT.render_stub_tests_yaml(cluster=cluster)
        runbook_dir = tmp_path / "AUTOGEN-abcdef0123456789"
        runbook_dir.mkdir()
        (runbook_dir / "RUNBOOK.md").write_text(runbook_md, encoding="utf-8")
        (runbook_dir / "tools.yaml").write_text(tools_yaml, encoding="utf-8")
        (runbook_dir / "checks.yaml").write_text(checks_yaml, encoding="utf-8")
        (runbook_dir / "tests.yaml").write_text(tests_yaml, encoding="utf-8")

        # When the loader walks the rendered quartet
        runbook = runbook_loader.load_runbook(runbook_dir)

        # Then the runbook loads cleanly with the autogen runbook_id and
        # the loader-computed content_sha (the template ships a PLACEHOLDER
        # that the F6.E pre-commit hook would rewrite, but the loader
        # always uses its own canonicalised hash)
        assert runbook.metadata.runbook_id == "AUTOGEN-abcdef0123456789"
        assert len(runbook.metadata.content_sha) == 32


# ---------------------------------------------------------------------------
# _open_draft_pr: subprocess capture + branch / --draft assertions
# ---------------------------------------------------------------------------


class TestOpenDraftPr:
    @pytest.mark.asyncio
    async def test_captures_branch_name_and_draft_flag_via_injected_runners(
        self, tmp_path: Path
    ) -> None:
        # Given a cluster, a captured-args runner, and a tmp repo root so
        # the rendered quartet writes somewhere harmless
        cluster = _make_cluster(fingerprint="0123456789abcdef", member_count=3)
        config = _make_flywheel_config()
        autogen_root = tmp_path / "src" / "sentinel" / "plugins" / "teams" / "sre" / "runbooks"
        autogen_root.mkdir(parents=True)
        captured_calls: list[tuple[str, ...]] = []

        async def _record_runner(args: Sequence[str]) -> Any:
            captured_calls.append(tuple(args))
            return _FLYWHEEL_SCRIPT.SubprocessResult(
                returncode=0,
                stdout="https://github.com/acme/sentinel/pull/123\n",
                stderr="",
            )

        # When _open_draft_pr runs end-to-end with the recording runner
        pr_url = await _FLYWHEEL_SCRIPT._open_draft_pr(
            cluster=cluster,
            today=date(2026, 4, 27),
            config=config,
            repo_root=tmp_path,
            autogen_root=autogen_root,
            subprocess_runner=_record_runner,
            git_runner=_record_runner,
        )

        # Then the captured commands include the branch (flywheel/runbook-gap-<fp>),
        # the --draft flag, and a title carrying the fingerprint; the PR URL
        # is extracted from gh's stdout
        assert pr_url == "https://github.com/acme/sentinel/pull/123"
        checkout_call = next(
            call for call in captured_calls if call[:3] == ("git", "checkout", "-b")
        )
        assert checkout_call[3] == "flywheel/runbook-gap-0123456789abcdef"
        gh_call = next(call for call in captured_calls if call[0] == "gh")
        assert "--draft" in gh_call
        title_index = gh_call.index("--title") + 1
        assert "0123456789abcdef" in gh_call[title_index]
