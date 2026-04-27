"""
Unit tests for ``scripts/runbook_drift_check.py`` (F6.L.4 / F6.L.6).

Covers:

* the three sweeps (fixture replay, stale runbook, tools registry) bound
  through the script's ``run_sweeps`` entry point;
* the dedup gate via :func:`persistence_drift.is_open_drift_recorded`;
* the Slack notifier path including the unconfigured no-op case;
* idempotency of the persist + notify combo over a re-run.

DB sessions are mocked. Slack adapter is a minimal stub conforming to the
notifier's Protocol surface so we don't reach into the real
``vendors.slack`` module here (its drift function is exercised by the
notifier tests transitively via the Protocol).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

import pytest

from sentinel.application.runbooks import _drift_notifier as drift_notifier_mod
from sentinel.domain.runbooks import drift as drift_mod
from sentinel.domain.runbooks import models as runbook_models


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "runbook_drift_check.py"
_MODULE_NAME = "sentinel_test_scripts.runbook_drift_check"


def _load_drift_check_module() -> ModuleType:
    """
    Load the drift-check script as an importable module.

    The script lives outside ``src/`` because it's a CI/cron entry point,
    not application code. ``importlib.util`` keeps the test isolated from
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


_DRIFT_CHECK = _load_drift_check_module()


# ---------------------------------------------------------------------------
# Fixture builders -- runbook composites with just enough fields
# ---------------------------------------------------------------------------


_DEFAULT_CONTENT_SHA = "a" * 32


def _make_runbook(
    *,
    runbook_id: str = "k8s-crashloop",
    owner: str = "sre-platform",
    last_validated: date | None = None,
    deprecated_at: date | None = None,
    tools: tuple[str, ...] = ("k8s_describe_pod",),
    tests: tuple[runbook_models.TestSpec, ...] = (),
    content_sha: str = _DEFAULT_CONTENT_SHA,
) -> runbook_models.Runbook:
    metadata = runbook_models.RunbookMetadata(
        runbook_id=runbook_id,
        description="Test runbook",
        content_sha=content_sha,
        applies_to=runbook_models.RunbookAppliesTo(
            alertnames=(),
            severity_min="P3",
            resource_kinds=(),
            exclude_labels={},
        ),
        tags=(),
        min_match_score=1,
        owner=owner,
        authors=("test-author",),
        last_validated=last_validated,
        deprecated_at=deprecated_at,
        superseded_by=None,
        mnpi_safe=True,
        canonical_sources=(),
    )
    tools_config = runbook_models.ToolsConfig(
        allowed_tools=tuple(runbook_models.ToolSpec(name=name, max_calls=1) for name in tools),
        denied_tools=(),
        max_total_tool_calls=10,
        max_loop_iterations=4,
    )
    checks_config = runbook_models.ChecksConfig(
        prescribed_checks=(),
        groundedness_rules=(),
        body_sanitization=runbook_models.BodySanitizationConfig(),
    )
    return runbook_models.Runbook(
        metadata=metadata,
        body="# stub body",
        tools=tools_config,
        checks=checks_config,
        tests=tests,
        directory=Path("/tmp/runbook"),  # noqa: S108 — fake path; loader is not invoked in this test
    )


def _make_test_spec(
    *,
    fixture_id: str = "k8s-crashloop-sample",
    expected_runbook_id: str | None = "k8s-crashloop",
    match_method: str = "tag",
    min_tag_score: int | None = 2,
) -> runbook_models.TestSpec:
    expected = runbook_models.TestExpected(
        runbook_id=expected_runbook_id,
        match_method=match_method,
        min_tag_score=min_tag_score,
    )
    return runbook_models.TestSpec(
        id=fixture_id,
        alert_payload_path="fixtures/sample.json",
        expected=expected,
    )


def _make_match(
    *,
    runbook_id: str | None = "k8s-crashloop",
    match_method: str = "tag",
    tag_score: int | None = 2,
    content_sha: str | None = _DEFAULT_CONTENT_SHA,
) -> runbook_models.RunbookMatch:
    return runbook_models.RunbookMatch(
        matched_runbook_id=runbook_id,
        content_sha=content_sha,
        match_method=match_method,
        confidence=1.0,
        tag_score=tag_score,
        llm_choice=None,
        llm_justification=None,
        candidates=(),
    )


def _make_count_session(*, count: int) -> mock.AsyncMock:
    """
    Return an AsyncMock session whose ``execute`` returns a result with
    ``scalar_one() == count``.

    Used for both the stale-runbook sweep (counts ``runbook_match`` rows
    in lookback) and the dedup gate (counts open drift rows).
    """
    session = mock.AsyncMock()
    result = mock.MagicMock()
    result.scalar_one.return_value = count
    session.execute = mock.AsyncMock(return_value=result)
    return session


class _StubSlackAdapter:
    """Minimal in-test Slack adapter satisfying the notifier's Protocol."""

    def __init__(self, *, configured: bool = True) -> None:
        self._configured = configured
        self.calls: list[dict[str, Any]] = []

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def post_drift_alert(
        self,
        *,
        channel: str,
        runbook_id: str,
        content_sha: str,
        drift_type: str,
        drift_severity: str,
        suggested_fix: str,
        resolution_pr_template_url: str,
    ) -> None:
        self.calls.append(
            {
                "channel": channel,
                "runbook_id": runbook_id,
                "content_sha": content_sha,
                "drift_type": drift_type,
                "drift_severity": drift_severity,
                "suggested_fix": suggested_fix,
                "resolution_pr_template_url": resolution_pr_template_url,
            }
        )


# ---------------------------------------------------------------------------
# Sweep 1: fixture-replay drift
# ---------------------------------------------------------------------------


class TestFixtureReplaySweep:
    @pytest.mark.asyncio
    async def test_emits_fixture_failure_when_matcher_returns_wrong_runbook(self) -> None:
        # Given a runbook with a fixture pinning expected_runbook_id="k8s-crashloop"
        fixture = _make_test_spec(expected_runbook_id="k8s-crashloop")
        runbook = _make_runbook(tests=(fixture,))
        runbooks = {runbook.runbook_id: runbook}

        # And a matcher that returns a different runbook id (simulating drift)
        async def wrong_matcher(_alert: Any, _runbooks: Any) -> runbook_models.RunbookMatch:
            return _make_match(runbook_id="some-other-runbook")

        # When the fixture-replay sweep runs
        events = await drift_mod.sweep_fixture_replays(runbooks=runbooks, matcher=wrong_matcher)

        # Then exactly one fixture_failure event is emitted with high severity
        assert len(events) == 1
        assert events[0].drift_type == "fixture_failure"
        assert events[0].drift_severity == "high"
        assert events[0].drift_detail["actual_runbook_id"] == "some-other-runbook"

    @pytest.mark.asyncio
    async def test_emits_min_tag_score_regression_when_score_under_floor(self) -> None:
        # Given a runbook with a fixture pinning min_tag_score=5
        fixture = _make_test_spec(min_tag_score=5)
        runbook = _make_runbook(tests=(fixture,))
        runbooks = {runbook.runbook_id: runbook}

        # And a matcher that returns the right runbook but with score=2
        async def low_score_matcher(_alert: Any, _runbooks: Any) -> runbook_models.RunbookMatch:
            return _make_match(tag_score=2)

        # When the sweep runs
        events = await drift_mod.sweep_fixture_replays(
            runbooks=runbooks, matcher=low_score_matcher
        )

        # Then a min_tag_score_regression event surfaces with medium severity
        assert len(events) == 1
        assert events[0].drift_type == "min_tag_score_regression"
        assert events[0].drift_severity == "medium"
        assert events[0].drift_detail["expected_min"] == 5
        assert events[0].drift_detail["actual_score"] == 2


# ---------------------------------------------------------------------------
# Sweep 2: stale-runbook drift
# ---------------------------------------------------------------------------


class TestStaleRunbookSweep:
    @pytest.mark.asyncio
    async def test_emits_stale_no_matches_for_unvalidated_unused_runbook(self) -> None:
        # Given a runbook last validated 100 days ago (past the 90-day threshold)
        today = date(2026, 4, 27)
        runbook = _make_runbook(last_validated=today - timedelta(days=100))
        runbooks = {runbook.runbook_id: runbook}

        # And a session whose match-row count returns zero (no traffic in lookback)
        session = _make_count_session(count=0)

        # When the stale sweep runs
        events = await drift_mod.sweep_stale_runbooks(
            session=session, runbooks=runbooks, today=today
        )

        # Then one stale_no_matches event surfaces with low severity
        assert len(events) == 1
        assert events[0].drift_type == "stale_no_matches"
        assert events[0].drift_severity == "low"
        assert events[0].drift_detail["days_since_validated"] == 100
        assert events[0].drift_detail["match_count_in_lookback"] == 0

    @pytest.mark.asyncio
    async def test_does_not_emit_when_runbook_has_recent_matches(self) -> None:
        # Given a stale runbook (100 days since validation)
        today = date(2026, 4, 27)
        runbook = _make_runbook(last_validated=today - timedelta(days=100))
        runbooks = {runbook.runbook_id: runbook}

        # And a session whose match-row count returns a non-zero count
        session = _make_count_session(count=3)

        # When the stale sweep runs
        events = await drift_mod.sweep_stale_runbooks(
            session=session, runbooks=runbooks, today=today
        )

        # Then no events are emitted (the runbook is being used)
        assert events == ()

    @pytest.mark.asyncio
    async def test_skips_runbook_inside_freshness_window(self) -> None:
        # Given a runbook last validated 10 days ago (well under the 90-day threshold)
        today = date(2026, 4, 27)
        runbook = _make_runbook(last_validated=today - timedelta(days=10))
        runbooks = {runbook.runbook_id: runbook}
        session = _make_count_session(count=0)

        # When the stale sweep runs
        events = await drift_mod.sweep_stale_runbooks(
            session=session, runbooks=runbooks, today=today
        )

        # Then no events surface and the DB query is never issued
        assert events == ()
        session.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sweep 3: tools-registry drift
# ---------------------------------------------------------------------------


class TestToolsRegistrySweep:
    def test_emits_tools_yaml_invalid_with_sorted_missing_names(self) -> None:
        # Given a runbook listing one valid + two missing tool names
        runbook = _make_runbook(tools=("nonexistent_two", "k8s_describe_pod", "nonexistent_one"))
        runbooks = {runbook.runbook_id: runbook}
        registry = frozenset({"k8s_describe_pod"})

        # When the tools sweep runs
        events = drift_mod.sweep_tools_registry(runbooks=runbooks, tool_registry=registry)

        # Then one tools_yaml_invalid event is emitted with sorted missing names
        assert len(events) == 1
        assert events[0].drift_type == "tools_yaml_invalid"
        assert events[0].drift_severity == "high"
        assert tuple(events[0].drift_detail["missing_tool_names"]) == (
            "nonexistent_one",
            "nonexistent_two",
        )

    def test_no_op_when_registry_empty(self) -> None:
        # Given a runbook with one tool and an empty registry
        runbook = _make_runbook(tools=("any_tool",))
        runbooks = {runbook.runbook_id: runbook}

        # When the tools sweep runs against an empty registry
        events = drift_mod.sweep_tools_registry(runbooks=runbooks, tool_registry=frozenset())

        # Then the sweep no-ops (registry not configured == cannot validate)
        assert events == ()


# ---------------------------------------------------------------------------
# Slack notifier path
# ---------------------------------------------------------------------------


class TestNotifyDrift:
    @pytest.mark.asyncio
    async def test_posts_payload_with_runbook_metadata_and_fallback_channel(self) -> None:
        # Given a tools_yaml_invalid drift event for a runbook with no team override
        event = drift_mod.DriftEvent(
            runbook_id="k8s-crashloop",
            runbook_content_sha=_DEFAULT_CONTENT_SHA,
            drift_type="tools_yaml_invalid",
            drift_severity="high",
            drift_detail={"missing_tool_names": ("nonexistent",)},
        )
        slack_adapter = _StubSlackAdapter(configured=True)

        # When notify_drift is invoked with a configured fallback channel
        await drift_notifier_mod.notify_drift(
            event=event,
            runbook_owner="sre-platform",
            slack_adapter=slack_adapter,
            fallback_channel="#sre-runbook-owners",
        )

        # Then the adapter is called once with the resolved channel + drift payload
        assert len(slack_adapter.calls) == 1
        call = slack_adapter.calls[0]
        assert call["channel"] == "#sre-runbook-owners"
        assert call["runbook_id"] == "k8s-crashloop"
        assert call["drift_type"] == "tools_yaml_invalid"
        assert call["drift_severity"] == "high"
        assert call["suggested_fix"]  # non-empty for known drift_type

    @pytest.mark.asyncio
    async def test_no_op_when_slack_adapter_unconfigured(self) -> None:
        # Given an unconfigured Slack adapter (vendor-adapter no-op convention)
        event = drift_mod.DriftEvent(
            runbook_id="k8s-crashloop",
            runbook_content_sha=_DEFAULT_CONTENT_SHA,
            drift_type="stale_no_matches",
            drift_severity="low",
            drift_detail={"days_since_validated": 100},
        )
        slack_adapter = _StubSlackAdapter(configured=False)

        # When notify_drift is invoked
        await drift_notifier_mod.notify_drift(
            event=event,
            runbook_owner="sre-platform",
            slack_adapter=slack_adapter,
            fallback_channel="#sre-runbook-owners",
        )

        # Then the adapter is never called (no Slack message attempted)
        assert slack_adapter.calls == []

    @pytest.mark.asyncio
    async def test_no_op_when_no_channel_resolves(self) -> None:
        # Given a drift event with an unmapped owner and an empty fallback channel
        event = drift_mod.DriftEvent(
            runbook_id="k8s-crashloop",
            runbook_content_sha=_DEFAULT_CONTENT_SHA,
            drift_type="fixture_failure",
            drift_severity="high",
            drift_detail={"fixture_id": "x"},
        )
        slack_adapter = _StubSlackAdapter(configured=True)

        # When notify_drift is invoked with no fallback
        await drift_notifier_mod.notify_drift(
            event=event,
            runbook_owner=None,
            slack_adapter=slack_adapter,
            fallback_channel="",
        )

        # Then the adapter is never called (drift logged only)
        assert slack_adapter.calls == []

    @pytest.mark.asyncio
    async def test_swallows_slack_exception_so_sweep_continues(self) -> None:
        # Given a Slack adapter whose post raises (simulating an outage)
        event = drift_mod.DriftEvent(
            runbook_id="k8s-crashloop",
            runbook_content_sha=_DEFAULT_CONTENT_SHA,
            drift_type="fixture_failure",
            drift_severity="high",
            drift_detail={"fixture_id": "x"},
        )

        class _ExplodingAdapter:
            is_configured = True

            async def post_drift_alert(self, **_: Any) -> None:
                raise RuntimeError("slack down")

        # When notify_drift is invoked
        # Then the exception is swallowed so the sweep can continue
        await drift_notifier_mod.notify_drift(
            event=event,
            runbook_owner="sre-platform",
            slack_adapter=_ExplodingAdapter(),
            fallback_channel="#sre-runbook-owners",
        )


# ---------------------------------------------------------------------------
# Idempotency: dedup gate + persist + notify
# ---------------------------------------------------------------------------


class TestPersistAndNotifyIdempotency:
    @pytest.mark.asyncio
    async def test_writes_and_notifies_when_no_open_row_exists(self) -> None:
        # Given a fresh drift event and a session that returns no matching open row
        event = drift_mod.DriftEvent(
            runbook_id="k8s-crashloop",
            runbook_content_sha=_DEFAULT_CONTENT_SHA,
            drift_type="tools_yaml_invalid",
            drift_severity="high",
            drift_detail={"missing_tool_names": ("nonexistent",)},
        )
        runbook = _make_runbook(owner="sre-platform")
        session = _make_count_session(count=0)
        slack_adapter = _StubSlackAdapter(configured=True)

        # When _persist_and_notify runs
        wrote = await _DRIFT_CHECK._persist_and_notify(
            event=event,
            runbooks={runbook.runbook_id: runbook},
            session=session,
            slack_adapter=slack_adapter,
            fallback_channel="#sre-runbook-owners",
        )

        # Then it returns True (fresh write) and the Slack adapter sees one call
        assert wrote is True
        assert len(slack_adapter.calls) == 1

    @pytest.mark.asyncio
    async def test_skips_when_open_row_already_exists(self) -> None:
        # Given a drift event the dedup gate already covers (count=1)
        event = drift_mod.DriftEvent(
            runbook_id="k8s-crashloop",
            runbook_content_sha=_DEFAULT_CONTENT_SHA,
            drift_type="tools_yaml_invalid",
            drift_severity="high",
            drift_detail={"missing_tool_names": ("nonexistent",)},
        )
        runbook = _make_runbook()
        session = _make_count_session(count=1)
        slack_adapter = _StubSlackAdapter(configured=True)

        # When _persist_and_notify runs
        wrote = await _DRIFT_CHECK._persist_and_notify(
            event=event,
            runbooks={runbook.runbook_id: runbook},
            session=session,
            slack_adapter=slack_adapter,
            fallback_channel="#sre-runbook-owners",
        )

        # Then it returns False (no write) and Slack is never called
        assert wrote is False
        assert slack_adapter.calls == []
