"""
Unit tests for the ``InvestigationState`` TypedDict (T15).

The TypedDict is a runtime-light contract: type-checker enforcement
provides most of its value at compile time. These tests lock in the
entry-time contract (envelope + alert required) and the convention for
optional keys filled progressively as nodes write to state.
"""

from __future__ import annotations

import uuid
from typing import get_type_hints

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.investigations import entities as investigation_entities
from sentinel.domain.runbooks import models as runbook_models
from sentinel.interfaces.workflows import sre_state as sre_state_mod
from tests import factories


class TestInvestigationState:
    def test_constructs_with_required_fields_only(self) -> None:
        # Given the two required entry-time inputs to an SRE investigation run:
        # an envelope minted at ingress and the inbound alert
        envelope = factories.make_envelope()
        alert = factories.make_alert()

        # When constructing an InvestigationState with only those keys
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
        }

        # Then both required keys round-trip and reference the same objects
        assert state["envelope"] is envelope
        assert state["alert"] is alert

    def test_constructs_with_all_fields_populated(self) -> None:
        # Given all possible state fields as they would appear after a complete run
        envelope = factories.make_envelope()
        alert = factories.make_alert()
        investigation = factories.make_investigation(alert=alert)
        runbook = factories.make_runbook()
        runbook_match_id = uuid.uuid4()
        confidence = factories.make_confidence_score(total=0.8)

        # When constructing a fully-populated InvestigationState
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "classification_category": "k8s",
            "runbook": runbook,
            "runbook_match": None,
            "runbook_match_id": runbook_match_id,
            "requires_approval": False,
            "investigation": investigation,
            "confidence": confidence,
            "needs_approval": False,
            "approval_decision": approval_entities.ApprovalDecision.APPROVED,
            "findings_published": True,
        }

        # Then every field round-trips correctly
        assert state["envelope"] is envelope
        assert state["alert"] is alert
        assert state["classification_category"] == "k8s"
        assert state["runbook"] is runbook
        assert state["runbook_match"] is None
        assert state["runbook_match_id"] == runbook_match_id
        assert state["requires_approval"] is False
        assert state["investigation"] is investigation
        assert state["confidence"] is confidence
        assert state["needs_approval"] is False
        assert state["approval_decision"] is approval_entities.ApprovalDecision.APPROVED
        assert state["findings_published"] is True

    def test_optional_fields_absent_when_not_set(self) -> None:
        # Given a minimal InvestigationState carrying only the entry-time inputs
        envelope = factories.make_envelope()
        alert = factories.make_alert()

        # When the state is constructed without any optional keys
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
        }

        # Then optional fields are absent from the dict (not present as None)
        assert "classification_category" not in state
        assert "runbook" not in state
        assert "runbook_match" not in state
        assert "runbook_match_id" not in state
        assert "requires_approval" not in state
        assert "investigation" not in state
        assert "confidence" not in state
        assert "needs_approval" not in state
        assert "approval_decision" not in state
        assert "findings_published" not in state

    def test_type_hints_match_design_spec(self) -> None:
        # Given the InvestigationState TypedDict
        # When inspecting its type hints
        hints = get_type_hints(sre_state_mod.InvestigationState)

        # Then every key from the design spec is present with the expected type
        assert hints["envelope"] is envelope_mod.Envelope
        assert hints["alert"] is alert_entities.Alert
        assert hints["classification_category"] is str
        assert hints["runbook"] == runbook_models.Runbook | None
        assert hints["runbook_match"] == runbook_models.RunbookMatch | None
        assert hints["runbook_match_id"] == uuid.UUID | None
        assert hints["requires_approval"] is bool
        assert hints["investigation"] == investigation_entities.Investigation | None
        assert hints["confidence"] == confidence_entities.ConfidenceScore | None
        assert hints["needs_approval"] is bool
        assert hints["approval_decision"] == approval_entities.ApprovalDecision | None
        assert hints["findings_published"] is bool
