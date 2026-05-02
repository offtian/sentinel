"""
T38 — Integration test: SRE workflow interrupt + approve path.

webhook → classify → match_runbook → investigate → analyse_root_cause
         → determine_confidence (low) → wait_for_human (INTERRUPT)
         → resume with approved=True → publish_findings → END

Uses ``InMemorySaver`` with pickle fallback (no Postgres required).
The graph is rebuilt after the first run to simulate the resume step.

Asserts:
- First ``investigate_alert`` call returns ``interrupt_payload`` (not None)
  and ``findings_published=False``
- ``resume_investigation(decision=APPROVED)`` returns
  ``findings_published=True``
- After resume the ``approval_decision`` is ``APPROVED``
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
# Shared fake-agent helpers (mirrors T37 pattern)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _FakeUsage:
    """Stub usage data with zero tokens for test doubles."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclasses.dataclass(frozen=True)
class _FakeResult[T]:
    """Generic fake PydanticAI result wrapping a typed output."""

    output: T

    def usage(self) -> _FakeUsage:
        """Return zero-token usage."""
        return _FakeUsage()

    def all_messages(self) -> list:
        """Return empty message list."""
        return []


async def _fake_classify_alert_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return a deterministic infrastructure classification."""
    return _FakeResult(
        alert_classifier.AlertClassification(
            severity="medium",
            affected_service="api-service",
            category="infrastructure",
            summary="Elevated error rate on api-service",
            requires_immediate_action=False,
        )
    )


async def _fake_investigator_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return empty findings to trigger the evidence floor (low confidence)."""
    return _FakeResult(
        investigator.InvestigationFindings(
            summary="",
            sources_queried=[],
            tool_calls=[],
        )
    )


async def _fake_root_cause_analyser_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
    """Return a low-confidence root cause analysis (no real evidence)."""
    return _FakeResult(
        root_cause_analyser.RootCauseAnalysis(
            root_cause="Possible misconfiguration — details unclear without data",
            confidence=0.25,
            evidence=[],
            remediation_steps=["Collect logs", "Escalate to on-call team"],
            affected_services=["api-service"],
            timeline="",
        )
    )


def _make_fake_agent(fake_run: Any) -> mock.MagicMock:
    """Return a mock agent whose ``.run`` is the supplied async callable."""
    agent = mock.MagicMock()
    agent.run = fake_run
    return agent


def _build_fake_config(*, require_approval_below_confidence: float = 0.7) -> mock.MagicMock:
    """
    Return a mock config for the low-confidence / interrupt scenario.

    Sets ``require_approval_below_confidence=0.7`` (realistic production default)
    so a 0.25-confidence result triggers the approval gate.
    """
    cfg = mock.MagicMock()
    cfg.require_approval_below_confidence = require_approval_below_confidence
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
    """Stub match_runbook that signals requires_approval=False."""
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


class TestSreWorkflowInterruptApprove:
    @pytest.mark.asyncio
    async def test_low_confidence_interrupts_then_approve_publishes(self) -> None:
        """
        Low-confidence run pauses at wait_for_human; approve resumes to publish.

        The same ``InMemorySaver`` instance is reused across both calls so the
        checkpoint persists between ``investigate_alert`` and
        ``resume_investigation``. In production, ``AsyncPostgresSaver`` stores
        the checkpoint in Postgres.
        """
        # Given a persistent in-memory checkpointer shared across both calls
        checkpointer = _make_memory_saver()
        fake_config = _build_fake_config(require_approval_below_confidence=0.7)
        request_id = uuid.uuid4()
        alert = factories.make_alert(
            alert_id=str(request_id)[:8],
            title="Elevated error rate on api-service",
        )
        envelope = factories.make_envelope(request_id=request_id)

        # When the first run fires and hits the low-confidence gate
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
        assert initial_outcome.interrupt_payload["action"] == "approve_investigation"
        assert initial_outcome.interrupt_payload["request_id"] == str(request_id)
        assert initial_outcome.findings_published is False

        # When the approval endpoint resumes the workflow with decision=APPROVED
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            resume_outcome = await sre_mod.resume_investigation(
                request_id=request_id,
                decision=approval_entities.ApprovalDecision.APPROVED,
                graph=graph,
                approver="ops-engineer",
                reason="LGTM — remediation steps are appropriate",
            )

        # Then the workflow completed successfully with published findings
        assert resume_outcome.findings_published is True
        assert resume_outcome.approval_decision is approval_entities.ApprovalDecision.APPROVED

    @pytest.mark.asyncio
    async def test_interrupt_payload_carries_investigation_summary(self) -> None:
        """
        The interrupt payload includes the root cause summary for human review.
        """
        # Given a low-confidence run setup
        checkpointer = _make_memory_saver()
        fake_config = _build_fake_config(require_approval_below_confidence=0.7)
        request_id = uuid.uuid4()
        alert = factories.make_alert(alert_id=str(request_id)[:8])
        envelope = factories.make_envelope(request_id=request_id)

        # When the workflow fires and pauses
        with (
            mock.patch.object(sre_mod, "get_config", return_value=fake_config),
            mock.patch.object(sre_mod, "match_runbook", _fake_match_runbook_no_approval),
            mock.patch.object(slack_mod, "post_investigation_summary", return_value=None),
        ):
            graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)
            outcome = await sre_mod.investigate_alert(
                alert=alert,
                envelope=envelope,
                graph=graph,
            )

        # Then the interrupt payload carries the LLM-produced root cause
        assert outcome.interrupt_payload is not None
        assert outcome.interrupt_payload.get("root_cause") is not None
        assert len(outcome.interrupt_payload["root_cause"]) > 0
        # And the confidence info is surfaced for the reviewer
        assert "confidence_total" in outcome.interrupt_payload
        assert "confidence_label" in outcome.interrupt_payload
