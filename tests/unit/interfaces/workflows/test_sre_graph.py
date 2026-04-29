"""
Unit tests for the SRE investigation graph builder.

The builder composes the seven LangGraph node functions plus the
``_route_after_confidence`` and ``_route_after_approval`` conditional
edges into a compiled ``StateGraph`` ready for ``ainvoke``. Tests cover
the structural shape of the compiled graph (nodes registered, edges
wired, conditional branches present) without exercising a real workflow
run -- that lives in the integration suite.

Covers tasks T24-T26 of the LangGraph SRE migration plan.
"""

from __future__ import annotations

from unittest import mock

from langgraph.checkpoint import memory as lg_memory
from langgraph.graph import state as lg_state

from sentinel.interfaces.workflows import sre_investigation as sre_mod


class TestRouteAfterConfidence:
    def test_routes_to_wait_for_human_when_approval_needed(self) -> None:
        # Given a state where needs_approval is True
        state = {
            "needs_approval": True,
            "envelope": mock.MagicMock(),
            "alert": mock.MagicMock(),
        }

        # When _route_after_confidence evaluates the state
        result = sre_mod._route_after_confidence(state)  # type: ignore[arg-type]

        # Then the route goes to the approval gate
        assert result == "wait_for_human"

    def test_routes_to_publish_findings_when_no_approval_needed(self) -> None:
        # Given a state where needs_approval is False
        state = {
            "needs_approval": False,
            "envelope": mock.MagicMock(),
            "alert": mock.MagicMock(),
        }

        # When _route_after_confidence evaluates the state
        result = sre_mod._route_after_confidence(state)  # type: ignore[arg-type]

        # Then the route skips the gate and goes straight to publish
        assert result == "publish_findings"


class TestRouteAfterApproval:
    def test_routes_to_publish_findings_when_approved(self) -> None:
        # Given a state with an APPROVED decision
        from sentinel.domain.approval import entities as approval_entities

        state = {
            "approval_decision": approval_entities.ApprovalDecision.APPROVED,
            "envelope": mock.MagicMock(),
            "alert": mock.MagicMock(),
        }

        # When _route_after_approval evaluates the state
        result = sre_mod._route_after_approval(state)  # type: ignore[arg-type]

        # Then the route goes to publish_findings
        assert result == "publish_findings"

    def test_routes_to_end_when_rejected(self) -> None:
        # Given a state with a REJECTED decision
        from langgraph import graph as lg_graph

        from sentinel.domain.approval import entities as approval_entities

        state = {
            "approval_decision": approval_entities.ApprovalDecision.REJECTED,
            "envelope": mock.MagicMock(),
            "alert": mock.MagicMock(),
        }

        # When _route_after_approval evaluates the state
        result = sre_mod._route_after_approval(state)  # type: ignore[arg-type]

        # Then the route terminates at END
        assert result == lg_graph.END

    def test_routes_to_end_when_no_decision_set(self) -> None:
        # Given a state with no approval_decision (missing key)
        from langgraph import graph as lg_graph

        state = {
            "envelope": mock.MagicMock(),
            "alert": mock.MagicMock(),
        }

        # When _route_after_approval evaluates the state
        result = sre_mod._route_after_approval(state)  # type: ignore[arg-type]

        # Then the route terminates at END (not approved)
        assert result == lg_graph.END


class TestBuildSreInvestigationGraph:
    def test_returns_compiled_state_graph(self) -> None:
        # Given an in-memory checkpointer (no Postgres needed for shape checks)
        checkpointer = lg_memory.InMemorySaver()

        # When the SRE investigation graph is built
        graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)

        # Then the result is a CompiledStateGraph from langgraph.graph.state
        assert isinstance(graph, lg_state.CompiledStateGraph)

    def test_registers_all_seven_named_nodes(self) -> None:
        # Given the graph
        checkpointer = lg_memory.InMemorySaver()

        # When the SRE investigation graph is built
        graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)

        # Then every expected node name is present in the compiled graph
        node_names = set(graph.get_graph().nodes.keys())
        expected_nodes = {
            "classify_alert",
            "match_runbook",
            "investigate",
            "analyse_root_cause",
            "determine_confidence",
            "wait_for_human",
            "publish_findings",
        }
        assert expected_nodes.issubset(node_names)

    def test_wires_linear_pipeline_edges(self) -> None:
        # Given the compiled graph
        checkpointer = lg_memory.InMemorySaver()
        graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)

        # When inspecting the static edge set
        adjacency: dict[str, set[str]] = {}
        for edge in graph.get_graph().edges:
            adjacency.setdefault(edge.source, set()).add(edge.target)

        # Then the linear happy-path is wired through the pipeline
        assert "classify_alert" in adjacency.get("__start__", set())
        assert "match_runbook" in adjacency.get("classify_alert", set())
        assert "investigate" in adjacency.get("match_runbook", set())
        assert "analyse_root_cause" in adjacency.get("investigate", set())
        assert "determine_confidence" in adjacency.get("analyse_root_cause", set())
        # And the publish_findings node terminates at END
        assert "__end__" in adjacency.get("publish_findings", set())

    def test_branches_after_determine_confidence(self) -> None:
        # Given the compiled graph
        checkpointer = lg_memory.InMemorySaver()
        graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)

        # When inspecting outgoing edges from determine_confidence
        targets: set[str] = set()
        for edge in graph.get_graph().edges:
            if edge.source == "determine_confidence":
                targets.add(edge.target)

        # Then both wait_for_human and publish_findings are reachable
        assert "wait_for_human" in targets
        assert "publish_findings" in targets

    def test_branches_after_wait_for_human(self) -> None:
        # Given the compiled graph
        checkpointer = lg_memory.InMemorySaver()
        graph = sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)

        # When inspecting outgoing edges from wait_for_human
        targets: set[str] = set()
        for edge in graph.get_graph().edges:
            if edge.source == "wait_for_human":
                targets.add(edge.target)

        # Then both publish_findings and END are reachable from the approval gate
        assert "publish_findings" in targets
        assert "__end__" in targets

    def test_passes_checkpointer_to_compile(self) -> None:
        # Given a checkpointer instance the test can identify
        checkpointer = lg_memory.InMemorySaver()
        captured: dict[str, object] = {}

        # And a StateGraph spy that records the value forwarded to ``compile``
        original_state_graph = sre_mod.lg_graph.StateGraph

        def spy_state_graph(*args: object, **kwargs: object) -> object:
            instance = original_state_graph(*args, **kwargs)
            original_compile = instance.compile

            def spy_compile(*c_args: object, **c_kwargs: object) -> object:
                captured["checkpointer"] = c_kwargs.get("checkpointer")
                return original_compile(*c_args, **c_kwargs)

            instance.compile = spy_compile  # type: ignore[method-assign]
            return instance

        # When build_sre_investigation_graph compiles the graph through the spy
        with mock.patch.object(
            sre_mod.lg_graph,
            "StateGraph",
            side_effect=spy_state_graph,
        ):
            sre_mod.build_sre_investigation_graph(checkpointer=checkpointer)

        # Then the checkpointer instance was forwarded to ``compile``
        assert captured["checkpointer"] is checkpointer
