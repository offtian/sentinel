"""
Unit tests for the ``investigate_alert``, ``resume_investigation``, and
``get_investigation_status`` entrypoints exposed by
``interfaces/workflows/sre_investigation.py``.

These wrap the LangGraph compiled SRE investigation graph; the tests use
a mock graph to assert the entrypoint's contract:

- ``investigate_alert`` invokes ``graph.ainvoke`` with a seeded TypedDict
  state and a config carrying ``thread_id = str(envelope.request_id)``.
- ``investigate_alert`` returns an ``InvestigationOutcome`` carrying the
  classification_category, root_cause, confidence, and approval flag from
  the final state.
- ``investigate_alert`` surfaces the LangGraph interrupt payload when the
  graph paused at the approval gate.
- ``resume_investigation`` invokes ``graph.ainvoke`` with a
  ``Command(resume=...)`` payload mapping ``ApprovalDecision`` onto
  ``approved`` and forwarding the optional approver / reason fields.
- ``get_investigation_status`` reads the checkpoint snapshot and
  classifies the thread as pending/approved/rejected/completed.

Covers tasks T27-T30 of the LangGraph SRE migration plan.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest import mock

import pytest
from langgraph import types as lg_types

from sentinel.domain.approval import entities as approval_entities
from sentinel.interfaces.workflows import sre_investigation as sre_mod
from tests import factories


class _FakeInterrupt:
    """Minimal stand-in for ``langgraph.types.Interrupt`` -- only the ``.value`` slot is read."""

    def __init__(self, value: Any) -> None:
        self.value = value


class TestInvestigateAlert:
    @pytest.mark.asyncio
    async def test_seeds_initial_state_with_envelope_and_alert(self) -> None:
        # Given a mock compiled graph whose ainvoke returns a fully completed final state
        alert = factories.make_alert()
        envelope = factories.make_envelope(request_id=uuid.uuid4())
        investigation = factories.make_investigation(alert=alert, root_cause="Memory leak")
        final_state: dict[str, Any] = {
            "envelope": envelope,
            "alert": alert,
            "classification_category": "k8s",
            "investigation": investigation,
            "confidence": factories.make_confidence_score(total=0.85),
            "needs_approval": False,
            "findings_published": True,
            "approval_decision": None,
        }
        graph = mock.MagicMock()
        graph.ainvoke = mock.AsyncMock(return_value=final_state)

        # When investigate_alert runs the graph
        outcome = await sre_mod.investigate_alert(
            alert=alert,
            envelope=envelope,
            graph=graph,
        )

        # Then the graph received a TypedDict state seeded with envelope and alert
        graph.ainvoke.assert_awaited_once()
        seeded_state = graph.ainvoke.await_args.args[0]
        assert seeded_state["envelope"] is envelope
        assert seeded_state["alert"] is alert
        # And optional keys are absent from the seed state
        assert "investigation" not in seeded_state
        assert "confidence" not in seeded_state

        # And the config keys the run by request_id
        assert graph.ainvoke.await_args.kwargs["config"] == {
            "configurable": {"thread_id": str(envelope.request_id)},
        }

        # And the outcome carries no interrupt payload (graph ran to completion)
        assert outcome.request_id == envelope.request_id
        assert outcome.interrupt_payload is None
        assert outcome.needs_approval is False

    @pytest.mark.asyncio
    async def test_returns_completed_outcome_when_graph_runs_to_end(self) -> None:
        # Given a graph returning a populated final state
        alert = factories.make_alert()
        envelope = factories.make_envelope()
        investigation = factories.make_investigation(
            alert=alert,
            root_cause="CPU throttling",
            remediation="1. Increase CPU limits",
        )
        confidence = factories.make_confidence_score(total=0.85)
        graph = mock.MagicMock()
        graph.ainvoke = mock.AsyncMock(
            return_value={
                "classification_category": "performance",
                "investigation": investigation,
                "confidence": confidence,
                "needs_approval": False,
                "findings_published": True,
                "approval_decision": None,
            }
        )

        # When investigate_alert runs
        outcome = await sre_mod.investigate_alert(
            alert=alert,
            envelope=envelope,
            graph=graph,
        )

        # Then the outcome reflects the completed pipeline values
        assert outcome.classification_category == "performance"
        assert outcome.root_cause == "CPU throttling"
        assert outcome.remediation == "1. Increase CPU limits"
        assert outcome.confidence is confidence
        assert outcome.needs_approval is False
        assert outcome.findings_published is True
        assert outcome.interrupt_payload is None
        assert outcome.approval_decision is None

    @pytest.mark.asyncio
    async def test_surfaces_interrupt_payload_when_graph_pauses(self) -> None:
        # Given a paused state carrying an __interrupt__ entry
        alert = factories.make_alert()
        envelope = factories.make_envelope(request_id=uuid.uuid4())
        investigation = factories.make_investigation(alert=alert, root_cause="OOMKilled")
        confidence = factories.make_confidence_score(total=0.45)
        interrupt_value = {
            "action": "approve_investigation",
            "request_id": str(envelope.request_id),
            "root_cause": "OOMKilled",
            "confidence_total": 0.45,
            "confidence_label": "Low",
        }
        graph = mock.MagicMock()
        graph.ainvoke = mock.AsyncMock(
            return_value={
                "classification_category": "k8s",
                "investigation": investigation,
                "confidence": confidence,
                "needs_approval": True,
                "findings_published": False,
                "approval_decision": None,
                "__interrupt__": (_FakeInterrupt(interrupt_value),),
            }
        )

        # When investigate_alert runs the graph
        outcome = await sre_mod.investigate_alert(
            alert=alert,
            envelope=envelope,
            graph=graph,
        )

        # Then the outcome surfaces the interrupt payload and marks the run
        # as awaiting approval
        assert outcome.interrupt_payload == interrupt_value
        assert outcome.needs_approval is True
        assert outcome.approval_decision is None
        assert outcome.findings_published is False


class TestResumeInvestigation:
    @pytest.mark.asyncio
    async def test_resumes_graph_with_approve_command(self) -> None:
        # Given a mock graph returning a final state recording the approval
        request_id = uuid.uuid4()
        graph = mock.MagicMock()
        graph.ainvoke = mock.AsyncMock(
            return_value={
                "classification_category": "k8s",
                "approval_decision": approval_entities.ApprovalDecision.APPROVED,
                "needs_approval": True,
                "findings_published": True,
            }
        )

        # When resume_investigation forwards an APPROVED decision with an approver
        outcome = await sre_mod.resume_investigation(
            request_id=request_id,
            decision=approval_entities.ApprovalDecision.APPROVED,
            graph=graph,
            approver="alice@example.com",
        )

        # Then ainvoke received a Command(resume=...) carrying approved=True
        # and the approver, keyed by the same thread_id
        graph.ainvoke.assert_awaited_once()
        command = graph.ainvoke.await_args.args[0]
        assert isinstance(command, lg_types.Command)
        assert command.resume == {
            "approved": True,
            "approver": "alice@example.com",
        }
        assert graph.ainvoke.await_args.kwargs["config"] == {
            "configurable": {"thread_id": str(request_id)},
        }

        # And the outcome reflects the approval decision recorded by the graph
        assert outcome.approval_decision is approval_entities.ApprovalDecision.APPROVED
        assert outcome.request_id == request_id
        assert outcome.findings_published is True

    @pytest.mark.asyncio
    async def test_resumes_graph_with_reject_command_and_reason(self) -> None:
        # Given a mock graph returning a final state recording the rejection
        request_id = uuid.uuid4()
        graph = mock.MagicMock()
        graph.ainvoke = mock.AsyncMock(
            return_value={
                "classification_category": "k8s",
                "approval_decision": approval_entities.ApprovalDecision.REJECTED,
                "needs_approval": True,
                "findings_published": False,
            }
        )

        # When resume_investigation forwards a REJECTED decision with approver and reason
        outcome = await sre_mod.resume_investigation(
            request_id=request_id,
            decision=approval_entities.ApprovalDecision.REJECTED,
            graph=graph,
            approver="bob@example.com",
            reason="Diagnosis looks wrong",
        )

        # Then the resume payload encodes approved=False and the reason
        command = graph.ainvoke.await_args.args[0]
        assert command.resume == {
            "approved": False,
            "approver": "bob@example.com",
            "reason": "Diagnosis looks wrong",
        }
        assert outcome.approval_decision is approval_entities.ApprovalDecision.REJECTED
        assert outcome.findings_published is False

    @pytest.mark.asyncio
    async def test_omits_approver_and_reason_when_not_supplied(self) -> None:
        # Given a mock graph returning an approved final state
        request_id = uuid.uuid4()
        graph = mock.MagicMock()
        graph.ainvoke = mock.AsyncMock(
            return_value={
                "classification_category": "k8s",
                "approval_decision": approval_entities.ApprovalDecision.APPROVED,
                "findings_published": True,
            }
        )

        # When resume_investigation runs without approver/reason
        await sre_mod.resume_investigation(
            request_id=request_id,
            decision=approval_entities.ApprovalDecision.APPROVED,
            graph=graph,
        )

        # Then the resume payload only carries the boolean approval flag
        command = graph.ainvoke.await_args.args[0]
        assert command.resume == {"approved": True}


class _StateSnapshotStub:
    """Duck-typed stand-in for ``langgraph.types.StateSnapshot`` in tests."""

    def __init__(self, *, values: dict[str, Any], next_nodes: tuple[str, ...] = ()) -> None:
        self.values = values
        self.next = next_nodes


class TestGetInvestigationStatus:
    @pytest.mark.asyncio
    async def test_returns_none_when_thread_has_no_checkpoint(self) -> None:
        # Given the saver has no record of the thread (empty values dict)
        request_id = uuid.uuid4()
        graph = mock.MagicMock()
        graph.aget_state = mock.AsyncMock(return_value=_StateSnapshotStub(values={}))

        # When get_investigation_status looks up the thread
        status = await sre_mod.get_investigation_status(
            request_id=request_id,
            graph=graph,
        )

        # Then the helper signals not-found via None and used the
        # request_id-keyed thread config
        assert status is None
        graph.aget_state.assert_awaited_once_with(
            {"configurable": {"thread_id": str(request_id)}},
        )

    @pytest.mark.asyncio
    async def test_returns_pending_when_paused_at_wait_for_human(self) -> None:
        # Given a snapshot with the wait_for_human node still ahead
        request_id = uuid.uuid4()
        snapshot = _StateSnapshotStub(
            values={"needs_approval": True, "approval_decision": None},
            next_nodes=("wait_for_human",),
        )
        graph = mock.MagicMock()
        graph.aget_state = mock.AsyncMock(return_value=snapshot)

        # When get_investigation_status looks up the thread
        status = await sre_mod.get_investigation_status(
            request_id=request_id,
            graph=graph,
        )

        # Then the helper reports the thread as pending approval
        assert status is not None
        assert status.status == "pending"
        assert status.needs_approval is True
        assert status.approval_decision is None

    @pytest.mark.asyncio
    async def test_returns_approved_status_when_approval_decision_approved(self) -> None:
        # Given a snapshot whose approval_decision is APPROVED
        request_id = uuid.uuid4()
        snapshot = _StateSnapshotStub(
            values={
                "needs_approval": True,
                "approval_decision": approval_entities.ApprovalDecision.APPROVED,
            },
        )
        graph = mock.MagicMock()
        graph.aget_state = mock.AsyncMock(return_value=snapshot)

        # When get_investigation_status looks up the thread
        status = await sre_mod.get_investigation_status(
            request_id=request_id,
            graph=graph,
        )

        # Then the helper reports the thread as approved
        assert status is not None
        assert status.status == "approved"
        assert status.approval_decision is approval_entities.ApprovalDecision.APPROVED

    @pytest.mark.asyncio
    async def test_returns_rejected_status_when_approval_decision_rejected(self) -> None:
        # Given a snapshot whose approval_decision is REJECTED
        request_id = uuid.uuid4()
        snapshot = _StateSnapshotStub(
            values={
                "needs_approval": True,
                "approval_decision": approval_entities.ApprovalDecision.REJECTED,
            },
        )
        graph = mock.MagicMock()
        graph.aget_state = mock.AsyncMock(return_value=snapshot)

        # When get_investigation_status looks up the thread
        status = await sre_mod.get_investigation_status(
            request_id=request_id,
            graph=graph,
        )

        # Then the helper reports the thread as rejected
        assert status is not None
        assert status.status == "rejected"
        assert status.approval_decision is approval_entities.ApprovalDecision.REJECTED

    @pytest.mark.asyncio
    async def test_returns_completed_when_run_finished_without_approval_gate(self) -> None:
        # Given a snapshot whose run completed without ever needing approval
        request_id = uuid.uuid4()
        snapshot = _StateSnapshotStub(
            values={"needs_approval": False, "approval_decision": None},
        )
        graph = mock.MagicMock()
        graph.aget_state = mock.AsyncMock(return_value=snapshot)

        # When get_investigation_status looks up the thread
        status = await sre_mod.get_investigation_status(
            request_id=request_id,
            graph=graph,
        )

        # Then the helper reports the thread as completed
        assert status is not None
        assert status.status == "completed"
        assert status.needs_approval is False
        assert status.approval_decision is None
