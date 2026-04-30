"""
T41 — Integration test: Legacy Pydantic Graph vs LangGraph workflow parity.

Runs the same alert through both the legacy ``interfaces/graphs/investigation.py``
pipeline and the new ``interfaces/workflows/sre_investigation.py`` workflow
with identically-configured PydanticAI agent doubles. Asserts that the two
implementations produce equivalent output on the core fields that represent
semantic correctness.

Parity fields checked:
- ``root_cause`` is non-empty in both outputs
- ``confidence.label`` matches (both use ``ConfidenceScore.from_factors`` with
  the same inputs so the score is deterministic)
- ``requires_approval`` / ``needs_approval`` matches (both False when
  ``require_approval_below=0.0`` / ``require_approval_below_confidence=0.0``)
- ``classification_category`` from the workflow matches the agent's ``category``
  (the legacy pipeline stores it inside the Investigation entity, not the reply
  directly, so we compare the category-string the agent emitted)

Uses ``InMemorySaver`` with pickle fallback for the workflow; the legacy
pipeline needs no checkpointer.

NOTE: The legacy pipeline returns an ``InvestigationReply`` Pydantic model
whereas the LangGraph workflow returns an ``InvestigationOutcome`` attrs
frozen class. Comparison is field-by-field, not object equality.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any
from unittest import mock

import pytest
from langgraph.checkpoint import memory as lg_memory
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from sentinel.interfaces.graphs import common as legacy_common
from sentinel.interfaces.graphs import investigation as legacy_investigation
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    investigator,
    root_cause_analyser,
)
from sentinel.interfaces.workflows import sre_investigation as sre_mod
from sentinel.vendors import slack as slack_mod
from tests import factories


# ---------------------------------------------------------------------------
# Shared fake-agent doubles used by both pipelines
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _FakeUsage:
    """Stub zero-token usage."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclasses.dataclass(frozen=True)
class _FakeResult[T]:
    """Generic fake PydanticAI agent result."""

    output: T

    def usage(self) -> _FakeUsage:
        """Return zero-token usage."""
        return _FakeUsage()

    def all_messages(self) -> list:
        """Return empty message list."""
        return []


# Identical agent fakes for both pipelines. The root cause output and
# confidence are determined by these responses, so both runs receive the
# same inputs and must produce the same key fields.


async def _fake_classify_alert_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return a deterministic infrastructure classification."""
    return _FakeResult(
        alert_classifier.AlertClassification(
            severity="high",
            affected_service="api-service",
            category="infrastructure",
            summary="Pod OOMKilled causing 5xx errors",
            requires_immediate_action=True,
        )
    )


async def _fake_investigator_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return populated findings with real data returns to clear the evidence floor."""
    return _FakeResult(
        investigator.InvestigationFindings(
            summary="Memory ramp to 2.1Gi observed before OOMKill",
            sources_queried=["datadog_metrics", "kubernetes_events"],
            tool_calls=[
                investigator.ToolCallRecord(
                    tool="get_pod_metrics",
                    query="api-service memory",
                    result_kind="data",
                ),
            ],
        )
    )


async def _fake_root_cause_analyser_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return a high-confidence root cause analysis."""
    return _FakeResult(
        root_cause_analyser.RootCauseAnalysis(
            root_cause="Memory leak in request handler caused OOMKill",
            confidence=0.92,
            evidence=["Memory ramp from 1.2Gi to 2.1Gi", "Pod OOMKilled at 14:30"],
            remediation_steps=["Increase memory limit to 4Gi", "Deploy leak fix"],
            affected_services=["api-service"],
            timeline="14:20 ramp → 14:30 OOMKill",
        )
    )


def _make_fake_agent(fake_run: Any) -> mock.MagicMock:
    """Return a mock agent whose ``.run`` is the supplied async callable."""
    agent = mock.MagicMock()
    agent.run = fake_run
    return agent


_AGENTS: dict[str, Any] = {
    "alert_classifier": _make_fake_agent(_fake_classify_alert_run),
    "investigator": _make_fake_agent(_fake_investigator_run),
    "root_cause_analyser": _make_fake_agent(_fake_root_cause_analyser_run),
}


def _build_workflow_fake_config() -> mock.MagicMock:
    """Return a mock config for the LangGraph workflow."""
    cfg = mock.MagicMock()
    cfg.require_approval_below_confidence = 0.0
    cfg.post_to_slack = False
    cfg.runbooks = None
    cfg.k8s_adapter = None
    cfg.pagerduty_client = None
    cfg.investigator_toolsets = ()
    cfg.analyser_toolsets = ()
    cfg.agent_for = mock.MagicMock(side_effect=lambda name: _AGENTS.get(name, mock.MagicMock()))
    return cfg


async def _fake_match_runbook_no_approval(state: Any) -> dict[str, Any]:
    """Stub match_runbook returning requires_approval=False."""
    return {
        "runbook": None,
        "runbook_match": None,
        "runbook_match_id": None,
        "requires_approval": False,
    }


def _make_memory_saver() -> lg_memory.InMemorySaver:
    """Return an InMemorySaver with pickle fallback. Rationale: see T37 module."""
    return lg_memory.InMemorySaver(
        serde=JsonPlusSerializer(
            pickle_fallback=True,
            allowed_msgpack_modules=True,
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSreLegacyVsWorkflowParity:
    @pytest.mark.asyncio
    async def test_root_cause_is_non_empty_in_both_pipelines(self) -> None:
        """
        Both pipelines return a non-empty root cause string for the same alert.
        """
        # Given the same alert and envelope fed to both pipelines
        alert = factories.make_alert(
            alert_id="parity-root-cause-001",
            title="api-service OOMKilled",
            description="Pod memory limit exceeded",
        )
        envelope = factories.make_envelope(request_id=uuid.uuid4())

        # When the legacy pipeline runs
        with mock.patch.object(slack_mod, "post_investigation_summary", return_value=None):
            legacy_reply: legacy_common.InvestigationReply = (
                await legacy_investigation.investigate_alert(
                    alert,
                    envelope=envelope,
                    agent_for=lambda name: _AGENTS.get(name, mock.MagicMock()),
                    post_to_slack=False,
                    require_approval_below=0.0,
                )
            )

        # And when the LangGraph workflow runs
        workflow_config = _build_workflow_fake_config()
        with (
            mock.patch.object(sre_mod, "get_config", return_value=workflow_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=_make_memory_saver())
            workflow_outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # Then both produce non-empty root causes
        assert legacy_reply.root_cause is not None
        assert len(legacy_reply.root_cause) > 0
        assert workflow_outcome.root_cause is not None
        assert len(workflow_outcome.root_cause) > 0

    @pytest.mark.asyncio
    async def test_confidence_label_matches_between_pipelines(self) -> None:
        """
        Both pipelines compute the same confidence label for identical inputs.

        Both use ``ConfidenceScore.from_factors`` with the same evidence inputs
        (one data-returning tool call → source_count=1, relevance=0.92,
        recency=0.8) so the composite score and label must be equal.
        """
        # Given the same alert fed to both pipelines
        alert = factories.make_alert(alert_id="parity-confidence-001")
        envelope = factories.make_envelope(request_id=uuid.uuid4())

        # When both pipelines run
        with mock.patch.object(slack_mod, "post_investigation_summary", return_value=None):
            legacy_reply = await legacy_investigation.investigate_alert(
                alert,
                envelope=envelope,
                agent_for=lambda name: _AGENTS.get(name, mock.MagicMock()),
                post_to_slack=False,
                require_approval_below=0.0,
            )

        workflow_config = _build_workflow_fake_config()
        with (
            mock.patch.object(sre_mod, "get_config", return_value=workflow_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=_make_memory_saver())
            workflow_outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # Then the confidence labels match between both implementations
        assert legacy_reply.confidence is not None
        assert workflow_outcome.confidence is not None
        assert legacy_reply.confidence.label == workflow_outcome.confidence.label

    @pytest.mark.asyncio
    async def test_neither_pipeline_requires_approval_when_threshold_zero(self) -> None:
        """
        With ``require_approval_below=0.0`` neither pipeline triggers the approval gate.
        """
        # Given the same alert
        alert = factories.make_alert(alert_id="parity-approval-001")
        envelope = factories.make_envelope(request_id=uuid.uuid4())

        # When the legacy pipeline runs with approval disabled
        with mock.patch.object(slack_mod, "post_investigation_summary", return_value=None):
            legacy_reply = await legacy_investigation.investigate_alert(
                alert,
                envelope=envelope,
                agent_for=lambda name: _AGENTS.get(name, mock.MagicMock()),
                post_to_slack=False,
                require_approval_below=0.0,
            )

        # And the workflow runs with approval threshold = 0.0
        workflow_config = _build_workflow_fake_config()
        with (
            mock.patch.object(sre_mod, "get_config", return_value=workflow_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=_make_memory_saver())
            workflow_outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # Then neither pipeline required approval
        assert legacy_reply.approval_status is None  # legacy: None means no approval triggered
        assert workflow_outcome.needs_approval is False

    @pytest.mark.asyncio
    async def test_classification_category_consistent(self) -> None:
        """
        The workflow's classification_category matches the agent's emitted category.

        The legacy pipeline doesn't surface category in the reply, but the
        workflow surfaces it via ``InvestigationOutcome.classification_category``.
        We assert it matches what the fake agent returns ("infrastructure") to
        confirm the workflow correctly propagates the classifier output.
        """
        # Given an alert
        alert = factories.make_alert(alert_id="parity-category-001")
        envelope = factories.make_envelope(request_id=uuid.uuid4())

        # When the workflow runs
        workflow_config = _build_workflow_fake_config()
        with (
            mock.patch.object(sre_mod, "get_config", return_value=workflow_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=_make_memory_saver())
            workflow_outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # Then the workflow classification matches what the fake agent returns
        assert workflow_outcome.classification_category == "infrastructure"
