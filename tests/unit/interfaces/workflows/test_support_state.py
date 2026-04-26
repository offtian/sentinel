"""
Unit tests for the ``SupportReviewState`` TypedDict.

The TypedDict is a runtime-light contract: most of its value comes from
type-checker enforcement at compile time. These tests exist to lock in
the entry-time contract (envelope + ticket required) and the convention
for optional keys filled progressively as nodes write to state.
"""

from __future__ import annotations

from typing import get_type_hints

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.search import searcher as search_module
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs.agents import ticket_reviewer
from sentinel.interfaces.workflows import support_state as support_state_mod
from tests import factories


class TestSupportReviewState:
    def test_initial_state_with_required_keys_only(self) -> None:
        # Given the two required entry-time inputs to a support workflow run:
        # an envelope minted at ingress and the inbound Jira ticket
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()

        # When constructing a SupportReviewState dict with only those keys
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
        }

        # Then both required keys round-trip and reference the same objects
        assert state["envelope"] is envelope
        assert state["ticket"] is ticket

    def test_progressive_keys_default_to_none_or_empty_tuple(self) -> None:
        # Given a SupportReviewState that has progressed through every node
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        classification = ticket_reviewer.TicketClassification(
            category="account",
            urgency="high",
            required_expertise=["billing"],
            key_questions=["what plan?"],
            search_queries=["billing"],
        )
        suggestion = factories.make_response_suggestion()
        confidence = factories.make_confidence_score(total=0.55)

        # When optional keys are added incrementally as each node writes
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "classification": classification,
            "doc_results": (),
            "ticket_results": (),
            "response_suggestion": suggestion,
            "confidence": confidence,
            "needs_approval": True,
            "approval_decision": approval_entities.ApprovalDecision.PENDING,
        }

        # Then every optional key sits at its expected post-write value
        assert state["classification"] is classification
        assert state["doc_results"] == ()
        assert state["ticket_results"] == ()
        assert state["response_suggestion"] is suggestion
        assert state["confidence"] is confidence
        assert state["needs_approval"] is True
        assert state["approval_decision"] is approval_entities.ApprovalDecision.PENDING

    def test_type_hints_match_design_spec(self) -> None:
        # Given the SupportReviewState TypedDict
        # When inspecting its type hints
        hints = get_type_hints(support_state_mod.SupportReviewState)

        # Then every key from the design spec is present with the expected type
        assert hints["envelope"] is envelope_mod.Envelope
        assert hints["ticket"] is support_entities.Ticket
        # Optional keys carry None in their union
        assert hints["classification"] == ticket_reviewer.TicketClassification | None
        assert hints["doc_results"] == tuple[search_module.DocumentSearchResult, ...]
        assert hints["ticket_results"] == tuple[search_module.TicketSearchResult, ...]
        assert hints["response_suggestion"] == support_entities.ResponseSuggestion | None
        assert hints["confidence"] == confidence_entities.ConfidenceScore | None
        assert hints["needs_approval"] is bool
        assert hints["approval_decision"] == approval_entities.ApprovalDecision | None
