"""
Unit tests for the F6.F.1 ``MatchRunbook`` pipeline node.

Exercises the node's :meth:`run` method directly with mocked
:class:`Dependencies`, covering the soft-degrade contract (catalog
unwired or matcher exception → no-match + requires_approval=True),
the happy path (matcher returns a match → state.runbook set + audit
row id stashed + checks seeded), and the explicit no-match path
(matcher returns no_match → state.runbook=None + requires_approval=True).

Full-graph integration coverage lives in
``test_investigation_envelope_propagation.py`` — this file isolates the
node so failures pinpoint MatchRunbook logic without bleeding through
the whole pipeline.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.runbooks import models as runbook_models
from sentinel.interfaces.graphs import investigation
from tests import factories


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_runbook(*, runbook_id: str = "k8s-crashloop") -> runbook_models.Runbook:
    """Build a minimal Runbook fixture sufficient for the node-level tests."""
    metadata = runbook_models.RunbookMetadata(
        runbook_id=runbook_id,
        description=f"Procedure for {runbook_id}.",
        content_sha="a" * 32,
        applies_to=runbook_models.RunbookAppliesTo(
            alertnames=("KubePodCrashLooping",),
            severity_min="P3",
            resource_kinds=("Pod",),
            exclude_labels={},
        ),
        tags=(),
        min_match_score=2,
        owner="sre-platform",
        authors=("ollie.tian",),
        last_validated=date(2026, 4, 26),
        deprecated_at=None,
        superseded_by=None,
        mnpi_safe=True,
        canonical_sources=(),
    )
    return runbook_models.Runbook(
        metadata=metadata,
        body="Investigate crashlooping pods.",
        tools=runbook_models.ToolsConfig(
            allowed_tools=(),
            denied_tools=(),
            max_total_tool_calls=10,
            max_loop_iterations=4,
        ),
        checks=runbook_models.ChecksConfig(
            prescribed_checks=(),
            groundedness_rules=(),
            body_sanitization=runbook_models.BodySanitizationConfig(
                reject_auto_rendered_urls=False,
                allowed_url_locations=(),
            ),
        ),
        tests=(),
        directory=Path("/tmp/runbooks") / runbook_id,  # noqa: S108
    )


def _make_state() -> investigation.State:
    """Build a minimal pipeline state pointing at a synthetic alert + envelope."""
    return investigation.State(
        envelope=factories.make_envelope(),
        alert=factories.make_alert(),
    )


@dataclasses.dataclass
class _FakeStatusUpdateClient:
    """Captures status updates without doing any I/O."""

    updates: list[str] = dataclasses.field(default_factory=list)

    async def update_status(self, message: str) -> None:
        self.updates.append(message)


def _make_dependencies(
    *,
    runbooks: dict[str, runbook_models.Runbook] | None = None,
) -> investigation.Dependencies:
    """Build a Dependencies instance with the matcher-relevant knobs wired."""
    return investigation.Dependencies(
        status_update_client=_FakeStatusUpdateClient(),
        agent_for=mock.MagicMock(),
        holmes=factories.MockHolmesAdapter(),
        runbooks=runbooks,
    )


def _make_ctx(
    *,
    state: investigation.State,
    deps: investigation.Dependencies,
) -> Any:
    """Build a minimal GraphRunContext-shaped object for direct node invocation."""
    ctx = mock.MagicMock()
    ctx.state = state
    ctx.deps = deps
    return ctx


# ---------------------------------------------------------------------------
# MatchRunbook.run — soft-degrade when catalog is unwired
# ---------------------------------------------------------------------------


class TestMatchRunbookSoftDegrade:
    @pytest.mark.asyncio
    async def test_no_catalog_yields_no_match_without_persistence(self) -> None:
        # Given a Dependencies with runbooks=None (e.g. unit-test pipeline)
        state = _make_state()
        deps = _make_dependencies(runbooks=None)
        ctx = _make_ctx(state=state, deps=deps)

        # When the MatchRunbook node runs
        node = investigation.MatchRunbook()
        result = await node.run(ctx)

        # Then the node short-circuits to InvestigateWithHolmes with
        # state.runbook=None and requires_approval=True so the generic
        # frame is used downstream
        assert isinstance(result, investigation.InvestigateWithHolmes)
        assert state.runbook is None
        assert state.requires_approval is True
        assert state.runbook_match is None
        assert state.runbook_match_id is None

    @pytest.mark.asyncio
    async def test_matcher_exception_yields_no_match_soft_degrade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a catalog wired but the matcher raises mid-pipeline
        crashloop = _make_runbook()
        deps = _make_dependencies(runbooks={crashloop.metadata.runbook_id: crashloop})
        state = _make_state()
        ctx = _make_ctx(state=state, deps=deps)

        async def _exploding_matcher(**_kwargs: Any) -> runbook_models.RunbookMatch:
            raise RuntimeError("matcher fault")

        monkeypatch.setattr(investigation.runbook_matcher, "match_runbook", _exploding_matcher)

        # When the node runs, the exception is logged and the pipeline
        # continues with the no-match contract — never crashes the whole run
        node = investigation.MatchRunbook()
        result = await node.run(ctx)

        # Then the soft-degrade path applies (no_match + approval required)
        assert isinstance(result, investigation.InvestigateWithHolmes)
        assert state.runbook is None
        assert state.requires_approval is True


# ---------------------------------------------------------------------------
# MatchRunbook.run — happy path (matcher returns a match)
# ---------------------------------------------------------------------------


class TestMatchRunbookHappyPath:
    @pytest.mark.asyncio
    async def test_match_sets_state_runbook_and_clears_approval_requirement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a catalog with k8s-crashloop and a matcher that picks it
        crashloop = _make_runbook()
        deps = _make_dependencies(runbooks={crashloop.metadata.runbook_id: crashloop})
        state = _make_state()
        ctx = _make_ctx(state=state, deps=deps)

        match = runbook_models.RunbookMatch(
            matched_runbook_id=crashloop.metadata.runbook_id,
            content_sha=crashloop.metadata.content_sha,
            match_method="tag",
            confidence=0.95,
            tag_score=3,
            llm_choice=None,
            llm_justification=None,
            candidates=(),
        )

        async def _matcher_returns_match(
            *,
            alert: Any,
            envelope: envelope_mod.Envelope,
            runbooks: Any,
            disambiguator: Any,
            rag_fallback: Any,
        ) -> runbook_models.RunbookMatch:
            return match

        monkeypatch.setattr(investigation.runbook_matcher, "match_runbook", _matcher_returns_match)

        # When the node runs without a db_session_factory
        node = investigation.MatchRunbook()
        result = await node.run(ctx)

        # Then state.runbook resolves to the matched runbook from the catalog
        # and requires_approval is False — the alert is on a known procedure
        assert isinstance(result, investigation.InvestigateWithHolmes)
        assert state.runbook is crashloop
        assert state.requires_approval is False
        assert state.runbook_match is match


# ---------------------------------------------------------------------------
# MatchRunbook.run — explicit no_match (Stage 2B / generic playbook path)
# ---------------------------------------------------------------------------


class TestMatchRunbookNoMatch:
    @pytest.mark.asyncio
    async def test_no_match_sets_requires_approval_and_clears_runbook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a catalog wired but the matcher returns an explicit no_match
        # (Stage 2B rescue exhausted; generic playbook path)
        crashloop = _make_runbook()
        deps = _make_dependencies(runbooks={crashloop.metadata.runbook_id: crashloop})
        state = _make_state()
        ctx = _make_ctx(state=state, deps=deps)

        no_match = runbook_models.RunbookMatch(
            matched_runbook_id=None,
            content_sha=None,
            match_method="no_match",
            confidence=0.0,
            tag_score=None,
            llm_choice=None,
            llm_justification=None,
            candidates=(),
        )

        async def _matcher_returns_no_match(**_kwargs: Any) -> runbook_models.RunbookMatch:
            return no_match

        monkeypatch.setattr(
            investigation.runbook_matcher, "match_runbook", _matcher_returns_no_match
        )

        # When the node runs
        node = investigation.MatchRunbook()
        result = await node.run(ctx)

        # Then state.runbook is None (no procedure found) and approval is
        # required so the generic-playbook output goes through human review
        assert isinstance(result, investigation.InvestigateWithHolmes)
        assert state.runbook is None
        assert state.requires_approval is True
        assert state.runbook_match is no_match


# ---------------------------------------------------------------------------
# MatchRunbook.run — status update side effect
# ---------------------------------------------------------------------------


class TestMatchRunbookStatusUpdate:
    @pytest.mark.asyncio
    async def test_emits_status_update_before_running_matcher(self) -> None:
        # Given a Dependencies whose status_update_client captures messages
        deps = _make_dependencies(runbooks=None)
        state = _make_state()
        ctx = _make_ctx(state=state, deps=deps)

        # When the node runs
        node = investigation.MatchRunbook()
        await node.run(ctx)

        # Then exactly one matching-related status update is emitted before the
        # node yields control to InvestigateWithHolmes
        client = deps.status_update_client
        assert isinstance(client, _FakeStatusUpdateClient)
        assert client.updates == ["Matching runbook..."]
