"""
T43 — Integration test: SRE workflow state completeness after a happy-path run.

After a high-confidence happy-path run, the checkpointed state must contain
all fields that represent a complete, auditable investigation. This test
reads the checkpoint back via ``get_investigation_status`` and direct state
inspection to assert the expected fields are populated.

Asserts (from the final checkpoint state):
- ``alert`` — the original alert object is present
- ``classification_category`` — non-empty string from the classifier agent
- ``investigation`` — not None (created by classify_alert, updated by downstream nodes)
- ``confidence`` — not None (set by determine_confidence)
- ``findings_published`` — True (set by publish_findings on the happy path)

Uses ``InMemorySaver`` with pickle fallback (no Postgres required).
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any
from unittest import mock

import pytest
from langgraph.checkpoint import memory as lg_memory
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    investigator,
    root_cause_analyser,
)
from sentinel.interfaces.workflows import sre_investigation as sre_mod
from sentinel.vendors import slack as slack_mod
from tests import factories


# ---------------------------------------------------------------------------
# Fake agent helpers (high-confidence happy-path)
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


async def _fake_classify_alert_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return a deterministic high-severity infrastructure classification."""
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
    """Return populated investigation findings to clear the evidence floor."""
    return _FakeResult(
        investigator.InvestigationFindings(
            summary="Memory usage at 2.1Gi/2Gi limit before OOMKill",
            sources_queried=["datadog_metrics", "kubernetes_events"],
            tool_calls=[
                investigator.ToolCallRecord(
                    tool="get_pod_metrics",
                    query="api-service pod memory",
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
            evidence=[
                "Memory ramp from 1.2Gi to 2.1Gi over 10 minutes",
                "Pod OOMKilled at 14:30 UTC",
            ],
            remediation_steps=[
                "Increase memory limit to 4Gi",
                "Deploy memory-leak fix",
            ],
            affected_services=["api-service"],
            timeline="14:20 memory ramp → 14:30 OOMKill → 14:32 5xx spike",
        )
    )


def _make_fake_agent(fake_run: Any) -> mock.MagicMock:
    """Return a mock agent whose ``.run`` is the supplied async callable."""
    agent = mock.MagicMock()
    agent.run = fake_run
    return agent


def _build_fake_config() -> mock.MagicMock:
    """
    Return a mock config for a no-approval happy-path run.

    ``require_approval_below_confidence=0.0`` ensures all confidence scores
    skip the approval gate. ``post_to_slack=False`` prevents external I/O.
    """
    cfg = mock.MagicMock()
    cfg.require_approval_below_confidence = 0.0
    cfg.post_to_slack = False
    cfg.runbooks = None
    cfg.db_session_factory = None
    cfg.k8s_adapter = None
    cfg.pagerduty_client = None
    cfg.investigator_toolsets = ()
    cfg.analyser_toolsets = ()
    agents: dict[str, Any] = {
        "alert_classifier": _make_fake_agent(_fake_classify_alert_run),
        "investigator": _make_fake_agent(_fake_investigator_run),
        "root_cause_analyser": _make_fake_agent(_fake_root_cause_analyser_run),
    }
    cfg.agent_for = mock.MagicMock(side_effect=lambda name: agents.get(name, mock.MagicMock()))
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
    """
    Return an ``InMemorySaver`` with pickle fallback for attrs class support.

    See T37 test module for full rationale. Production uses ``AsyncPostgresSaver``.
    """
    return lg_memory.InMemorySaver(
        serde=JsonPlusSerializer(
            pickle_fallback=True,
            allowed_msgpack_modules=True,
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSreWorkflowStateCompleteness:
    @pytest.mark.asyncio
    async def test_checkpoint_contains_alert_after_happy_path(self) -> None:
        """
        The original alert object is present in the checkpointed state after the run.

        ``get_investigation_status`` does not expose the alert directly, so this
        test reads the raw checkpoint via ``graph.aget_state``.
        """
        # Given a happy-path run to completion
        saver = _make_memory_saver()
        request_id = uuid.uuid4()
        alert = factories.make_alert(
            alert_id="state-completeness-001",
            title="api-service OOMKilled",
        )
        envelope = factories.make_envelope(request_id=request_id)
        fake_config = _build_fake_config()

        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=saver)
            await sre_mod.investigate_alert(alert=alert, envelope=envelope, graph=graph)

        # When the final checkpoint state is retrieved
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(request_id)}})
        state_values: dict[str, Any] = getattr(snapshot, "values", {}) or {}

        # Then the original alert is present in the checkpoint
        assert "alert" in state_values
        assert state_values["alert"] is not None
        assert state_values["alert"].id == "state-completeness-001"

    @pytest.mark.asyncio
    async def test_checkpoint_contains_classification_category(self) -> None:
        """
        ``classification_category`` is a non-empty string in the final checkpoint.
        """
        # Given a completed happy-path run
        saver = _make_memory_saver()
        request_id = uuid.uuid4()
        alert = factories.make_alert(alert_id="state-category-001")
        envelope = factories.make_envelope(request_id=request_id)
        fake_config = _build_fake_config()

        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=saver)
            await sre_mod.investigate_alert(alert=alert, envelope=envelope, graph=graph)

        # When the checkpoint is read
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(request_id)}})
        state_values: dict[str, Any] = getattr(snapshot, "values", {}) or {}

        # Then classification_category is a non-empty string
        assert "classification_category" in state_values
        assert isinstance(state_values["classification_category"], str)
        assert len(state_values["classification_category"]) > 0

    @pytest.mark.asyncio
    async def test_checkpoint_contains_investigation_object(self) -> None:
        """
        ``investigation`` is not None in the final checkpoint.
        """
        # Given a completed happy-path run
        saver = _make_memory_saver()
        request_id = uuid.uuid4()
        alert = factories.make_alert(alert_id="state-investigation-001")
        envelope = factories.make_envelope(request_id=request_id)
        fake_config = _build_fake_config()

        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=saver)
            await sre_mod.investigate_alert(alert=alert, envelope=envelope, graph=graph)

        # When the checkpoint is read
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(request_id)}})
        state_values: dict[str, Any] = getattr(snapshot, "values", {}) or {}

        # Then investigation is present and not None
        assert "investigation" in state_values
        assert state_values["investigation"] is not None

    @pytest.mark.asyncio
    async def test_checkpoint_contains_confidence_score(self) -> None:
        """
        ``confidence`` is not None in the final checkpoint after determine_confidence runs.
        """
        # Given a completed happy-path run
        saver = _make_memory_saver()
        request_id = uuid.uuid4()
        alert = factories.make_alert(alert_id="state-confidence-001")
        envelope = factories.make_envelope(request_id=request_id)
        fake_config = _build_fake_config()

        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=saver)
            await sre_mod.investigate_alert(alert=alert, envelope=envelope, graph=graph)

        # When the checkpoint is read
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(request_id)}})
        state_values: dict[str, Any] = getattr(snapshot, "values", {}) or {}

        # Then confidence is present and not None
        assert "confidence" in state_values
        assert state_values["confidence"] is not None

    @pytest.mark.asyncio
    async def test_checkpoint_findings_published_true_on_happy_path(self) -> None:
        """
        ``findings_published`` is ``True`` in the checkpoint after a successful happy-path run.
        """
        # Given a completed happy-path run
        saver = _make_memory_saver()
        request_id = uuid.uuid4()
        alert = factories.make_alert(alert_id="state-published-001")
        envelope = factories.make_envelope(request_id=request_id)
        fake_config = _build_fake_config()

        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=saver)
            await sre_mod.investigate_alert(alert=alert, envelope=envelope, graph=graph)

        # When the checkpoint is read
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(request_id)}})
        state_values: dict[str, Any] = getattr(snapshot, "values", {}) or {}

        # Then findings_published is True
        assert "findings_published" in state_values
        assert state_values["findings_published"] is True

    @pytest.mark.asyncio
    async def test_all_required_state_fields_present_in_single_run(self) -> None:
        """
        A single happy-path run populates all five required state fields.

        This is the canonical completeness check — if any node fails to write
        its output key into the state, at least one assertion in this test fails.
        """
        # Given a fresh checkpointer and known alert
        saver = _make_memory_saver()
        request_id = uuid.uuid4()
        original_alert = factories.make_alert(
            alert_id="state-all-fields-001",
            title="Memory leak on api-service",
        )
        envelope = factories.make_envelope(request_id=request_id)
        fake_config = _build_fake_config()

        # When the happy-path workflow runs to completion
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=saver)
            outcome = await sre_mod.investigate_alert(
                alert=original_alert,
                envelope=envelope,
                graph=graph,
            )

        # And when the checkpoint is read directly
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(request_id)}})
        state_values: dict[str, Any] = getattr(snapshot, "values", {}) or {}

        # Then the outcome already confirms findings_published (fast path check)
        assert outcome.findings_published is True

        # And all five required fields are present and have the expected types/values
        assert "alert" in state_values, "alert missing from checkpoint"
        assert state_values["alert"] is not None
        assert state_values["alert"].id == "state-all-fields-001"

        assert "classification_category" in state_values, (
            "classification_category missing from checkpoint"
        )
        assert isinstance(state_values["classification_category"], str)
        assert len(state_values["classification_category"]) > 0

        assert "investigation" in state_values, "investigation missing from checkpoint"
        assert state_values["investigation"] is not None

        assert "confidence" in state_values, "confidence missing from checkpoint"
        assert state_values["confidence"] is not None

        assert "findings_published" in state_values, "findings_published missing from checkpoint"
        assert state_values["findings_published"] is True
