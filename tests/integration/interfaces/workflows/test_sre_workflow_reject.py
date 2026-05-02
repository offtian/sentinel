"""
T39 — Integration test: SRE workflow interrupt + reject path.

webhook → classify → match_runbook → investigate → analyse_root_cause
         → determine_confidence (low) → wait_for_human (INTERRUPT)
         → resume with approved=False → END (no publish_findings)

Uses ``InMemorySaver`` with pickle fallback (no Postgres required).

Asserts:
- First ``investigate_alert`` call returns ``interrupt_payload`` (not None)
- ``resume_investigation(decision=REJECTED)`` returns ``findings_published=False``
- The Slack ``post_investigation_summary`` is NOT called on the reject path
- ``approval_decision`` is ``REJECTED`` in the resume outcome
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
# Fake agent helpers (low-confidence scenario)
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
    """Return a low-urgency classification that won't inflate confidence."""
    return _FakeResult(
        alert_classifier.AlertClassification(
            severity="low",
            affected_service="background-worker",
            category="performance",
            summary="Slow job processing on background-worker",
            requires_immediate_action=False,
        )
    )


async def _fake_investigator_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return empty findings to trigger the evidence floor."""
    return _FakeResult(
        investigator.InvestigationFindings(
            summary="",
            sources_queried=[],
            tool_calls=[],
        )
    )


async def _fake_root_cause_analyser_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return a low-confidence analysis (no supporting evidence)."""
    return _FakeResult(
        root_cause_analyser.RootCauseAnalysis(
            root_cause="Undetermined — insufficient evidence for confident diagnosis",
            confidence=0.2,
            evidence=[],
            remediation_steps=["Collect more telemetry", "Re-run investigation with traces"],
            affected_services=["background-worker"],
            timeline="",
        )
    )


def _make_fake_agent(fake_run: Any) -> mock.MagicMock:
    """Return a mock agent whose ``.run`` is the supplied async callable."""
    agent = mock.MagicMock()
    agent.run = fake_run
    return agent


def _build_fake_config() -> mock.MagicMock:
    """
    Return a mock config for the low-confidence / reject scenario.

    ``require_approval_below_confidence=0.7`` ensures the 0.2-confidence
    run always triggers the approval gate.
    """
    cfg = mock.MagicMock()
    cfg.require_approval_below_confidence = 0.7
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
    Return an ``InMemorySaver`` with pickle fallback.

    Rationale: see T37 test module. Production uses ``AsyncPostgresSaver``.
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


class TestSreWorkflowReject:
    @pytest.mark.asyncio
    async def test_reject_does_not_publish_findings(self) -> None:
        """
        Reject path: workflow terminates at END without calling publish_findings.

        ``findings_published`` stays ``False`` on the reject path.
        """
        # Given a persistent checkpointer and low-confidence fake agents
        checkpointer = _make_memory_saver()
        fake_config = _build_fake_config()
        request_id = uuid.uuid4()
        alert = factories.make_alert(alert_id=str(request_id)[:8])
        envelope = factories.make_envelope(request_id=request_id)

        # When the workflow fires and pauses at the approval gate
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)
            initial_outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # Then the workflow paused at the approval gate
        assert initial_outcome.interrupt_payload is not None
        assert initial_outcome.findings_published is False

        # When the rejection decision is posted against the same request_id
        slack_spy = mock.AsyncMock(return_value=None)
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(slack_mod, "post_investigation_summary", slack_spy),
        ):
            reject_outcome = await sre_mod.resume_investigation(
                request_id=request_id,
                decision=approval_entities.ApprovalDecision.REJECTED,
                graph=graph,
                approver="ops-engineer",
                reason="Insufficient evidence — needs more data before action",
            )

        # Then findings were NOT published
        assert reject_outcome.findings_published is False

        # And the approval decision is REJECTED
        assert reject_outcome.approval_decision is approval_entities.ApprovalDecision.REJECTED

        # And the Slack publisher was never called (publish_findings was skipped)
        slack_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_reject_outcome_carries_approval_decision(self) -> None:
        """
        The resume outcome after rejection explicitly carries REJECTED decision.
        """
        # Given a run that interrupted at the approval gate
        checkpointer = _make_memory_saver()
        fake_config = _build_fake_config()
        request_id = uuid.uuid4()
        alert = factories.make_alert(alert_id=str(request_id)[:8])
        envelope = factories.make_envelope(request_id=request_id)

        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)
            await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # When the rejection is posted
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            outcome = await sre_mod.resume_investigation(
                request_id=request_id,
                decision=approval_entities.ApprovalDecision.REJECTED,
                graph=graph,
                approver="reviewer",
                reason="Not actionable",
            )

        # Then the outcome records REJECTED
        assert outcome.approval_decision is approval_entities.ApprovalDecision.REJECTED
        assert outcome.findings_published is False
