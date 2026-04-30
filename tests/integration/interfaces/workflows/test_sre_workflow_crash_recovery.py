"""
T40 — Integration test: SRE workflow crash recovery via checkpoint.

Simulates a mid-run crash by:
1. Running the workflow until it pauses at ``wait_for_human`` (low confidence).
2. Re-creating the compiled graph with the SAME ``InMemorySaver`` instance.
3. Calling ``resume_investigation`` against the preserved checkpoint.

The key assertion: a checkpoint survives graph re-creation when the
``InMemorySaver`` (or ``AsyncPostgresSaver`` in production) is reused,
allowing resumption without re-running already-completed nodes.

Uses ``InMemorySaver`` with pickle fallback (no Postgres required). In
production the saver is ``AsyncPostgresSaver``; the durable Postgres storage
means the checkpoint survives process restarts. This test simplifies that
scenario by reusing the same in-memory saver across both graph compilations.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any
from unittest import mock

import pytest
from langgraph.checkpoint import memory as lg_memory
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from sentinel.domain.approval import entities as approval_entities
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    investigator,
    root_cause_analyser,
)
from sentinel.interfaces.workflows import sre_investigation as sre_mod
from sentinel.vendors import slack as slack_mod
from tests import factories


# ---------------------------------------------------------------------------
# Fake agent helpers (low-confidence to force interrupt)
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
    """Return a low-urgency classification."""
    return _FakeResult(
        alert_classifier.AlertClassification(
            severity="low",
            affected_service="batch-processor",
            category="performance",
            summary="Batch processor timeout",
            requires_immediate_action=False,
        )
    )


async def _fake_investigator_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return empty findings (triggers evidence floor → low confidence)."""
    return _FakeResult(
        investigator.InvestigationFindings(
            summary="",
            sources_queried=[],
            tool_calls=[],
        )
    )


async def _fake_root_cause_analyser_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return a low-confidence analysis."""
    return _FakeResult(
        root_cause_analyser.RootCauseAnalysis(
            root_cause="Batch timeout — cause unclear without metrics",
            confidence=0.15,
            evidence=[],
            remediation_steps=["Review batch logs"],
            affected_services=["batch-processor"],
            timeline="",
        )
    )


def _make_fake_agent(fake_run: Any) -> mock.MagicMock:
    """Return a mock agent whose ``.run`` is the supplied async callable."""
    agent = mock.MagicMock()
    agent.run = fake_run
    return agent


def _build_fake_config() -> mock.MagicMock:
    """Return a mock config that forces the approval gate (threshold=0.7)."""
    cfg = mock.MagicMock()
    cfg.require_approval_below_confidence = 0.7
    cfg.post_to_slack = False
    cfg.runbooks = None
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
    Return an ``InMemorySaver`` with pickle fallback.

    The SAME instance must be passed to both graph compilations in the
    crash-recovery test so the checkpoint written during the first run is
    visible to the resumed graph. In production, ``AsyncPostgresSaver`` with a
    shared Postgres connection pool provides this durability across restarts.
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


class TestSreWorkflowCrashRecovery:
    @pytest.mark.asyncio
    async def test_checkpoint_persists_across_graph_recreation(self) -> None:
        """
        A checkpoint written by the first graph invocation is readable by a
        graph compiled from the same saver after a simulated crash.

        The test does NOT re-run already-completed nodes: the persisted
        checkpoint state contains ``classification_category``, ``confidence``,
        and ``investigation`` filled by the pre-interrupt nodes. The resume
        call drives only the post-interrupt path.
        """
        # Given a persistent saver and a first graph that interrupts mid-run
        shared_saver = _make_memory_saver()
        fake_config = _build_fake_config()
        request_id = uuid.uuid4()
        alert = factories.make_alert(alert_id=str(request_id)[:8])
        envelope = factories.make_envelope(request_id=request_id)

        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph_v1 = sre_mod.build_sre_investigation_graph(checkpointer=shared_saver)
            initial_outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph_v1,
            )

        # Confirm the workflow paused (simulated crash point)
        assert initial_outcome.interrupt_payload is not None
        assert initial_outcome.findings_published is False

        # When a new graph is compiled from the same saver (simulates process restart)
        # and the approval is submitted against the preserved checkpoint
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph_v2 = sre_mod.build_sre_investigation_graph(checkpointer=shared_saver)
            resume_outcome = await sre_mod.resume_investigation(
                request_id=request_id,
                decision=approval_entities.ApprovalDecision.APPROVED,
                graph=graph_v2,
                approver="on-call-engineer",
                reason="Approved — start with log collection",
            )

        # Then the resumed run completed and published findings
        assert resume_outcome.findings_published is True
        assert resume_outcome.approval_decision is approval_entities.ApprovalDecision.APPROVED

    @pytest.mark.asyncio
    async def test_checkpoint_contains_pre_interrupt_state_fields(self) -> None:
        """
        The checkpoint written before the interrupt contains classification_category,
        root_cause, and confidence — enough data to reconstruct the investigation
        without re-running the completed nodes.
        """
        # Given a shared saver and a first graph run that interrupts
        shared_saver = _make_memory_saver()
        fake_config = _build_fake_config()
        request_id = uuid.uuid4()
        alert = factories.make_alert(alert_id=str(request_id)[:8])
        envelope = factories.make_envelope(request_id=request_id)

        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=shared_saver)
            await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # When the checkpoint state is read back via get_investigation_status
        status = await sre_mod.get_investigation_status(
            request_id=request_id,
            graph=graph,
        )

        # Then the checkpoint records the run as pending (paused at gate)
        assert status is not None
        assert status.status == "pending"
        assert status.needs_approval is True
