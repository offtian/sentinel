"""
Unit tests for the support-review conditional-edge routing function.

The routing function is the only branching surface in the graph: it
inspects ``state["needs_approval"]`` and either routes to the
``wait_for_human`` interrupt node or terminates.

Covers task T13 of the LangGraph adoption plan.
"""

from __future__ import annotations

from langgraph import graph as lg_graph

from sentinel.interfaces.workflows import support_review as support_review_mod
from sentinel.interfaces.workflows import support_state as support_state_mod
from tests import factories


def _make_state(*, needs_approval: bool) -> support_state_mod.SupportReviewState:
    return {
        "envelope": factories.make_envelope(),
        "ticket": factories.make_ticket(),
        "needs_approval": needs_approval,
        "doc_results": (),
        "ticket_results": (),
    }


class TestRouteAfterConfidence:
    def test_returns_wait_for_human_when_needs_approval_is_true(self) -> None:
        # Given a state where determine_confidence flagged a low score
        state = _make_state(needs_approval=True)

        # When the routing function inspects the state
        next_node = support_review_mod._route_after_confidence(state)

        # Then it routes to the wait_for_human interrupt node
        assert next_node == "wait_for_human"

    def test_returns_end_when_needs_approval_is_false(self) -> None:
        # Given a state where determine_confidence cleared the approval gate
        state = _make_state(needs_approval=False)

        # When the routing function inspects the state
        next_node = support_review_mod._route_after_confidence(state)

        # Then it routes to the LangGraph END sentinel and terminates the run
        assert next_node == lg_graph.END
