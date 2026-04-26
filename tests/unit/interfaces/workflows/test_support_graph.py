"""
Unit tests for the support-review graph builder.

The builder composes the five LangGraph node functions plus the
``_route_after_confidence`` conditional edge into a compiled
``StateGraph`` ready for ``ainvoke``. Tests cover the structural
shape of the compiled graph (nodes registered, edges wired,
conditional branch present) without exercising a real workflow run --
that lives in the integration suite.

Covers task T14 of the LangGraph adoption plan.
"""

from __future__ import annotations

from unittest import mock

from langgraph.checkpoint import memory as lg_memory
from langgraph.graph import state as lg_state

from sentinel.interfaces.workflows import support_review as support_review_mod


class TestBuildSupportReviewGraph:
    def test_returns_compiled_state_graph(self) -> None:
        # Given an in-memory checkpointer (no Postgres needed for shape checks)
        checkpointer = lg_memory.InMemorySaver()

        # When the support-review graph is built
        graph = support_review_mod.build_support_review_graph(checkpointer=checkpointer)

        # Then the result is a CompiledStateGraph from langgraph.graph.state
        assert isinstance(graph, lg_state.CompiledStateGraph)

    def test_registers_all_five_named_nodes(self) -> None:
        # Given the graph
        checkpointer = lg_memory.InMemorySaver()

        # When the support-review graph is built
        graph = support_review_mod.build_support_review_graph(checkpointer=checkpointer)

        # Then every expected node name is present in the compiled graph
        node_names = set(graph.get_graph().nodes.keys())
        expected_nodes = {
            "classify_ticket",
            "search_documentation",
            "draft_response",
            "determine_confidence",
            "wait_for_human",
        }
        assert expected_nodes.issubset(node_names)

    def test_wires_linear_pipeline_edges(self) -> None:
        # Given the compiled graph
        checkpointer = lg_memory.InMemorySaver()
        graph = support_review_mod.build_support_review_graph(checkpointer=checkpointer)

        # When inspecting the static edge set
        adjacency: dict[str, set[str]] = {}
        for edge in graph.get_graph().edges:
            adjacency.setdefault(edge.source, set()).add(edge.target)

        # Then the linear happy-path is wired START -> classify -> search -> draft -> determine
        assert "classify_ticket" in adjacency.get("__start__", set())
        assert "search_documentation" in adjacency.get("classify_ticket", set())
        assert "draft_response" in adjacency.get("search_documentation", set())
        assert "determine_confidence" in adjacency.get("draft_response", set())
        # And the wait_for_human node terminates at END
        assert "__end__" in adjacency.get("wait_for_human", set())

    def test_branches_after_determine_confidence(self) -> None:
        # Given the compiled graph
        checkpointer = lg_memory.InMemorySaver()
        graph = support_review_mod.build_support_review_graph(checkpointer=checkpointer)

        # When inspecting outgoing edges from determine_confidence
        targets: set[str] = set()
        for edge in graph.get_graph().edges:
            if edge.source == "determine_confidence":
                targets.add(edge.target)

        # Then both wait_for_human and END are reachable from the branch
        assert "wait_for_human" in targets
        assert "__end__" in targets

    def test_passes_checkpointer_to_compile(self) -> None:
        # Given a checkpointer instance the test can identify
        checkpointer = lg_memory.InMemorySaver()
        captured: dict[str, object] = {}

        # And a StateGraph spy that records the value forwarded to ``compile``
        original_state_graph = support_review_mod.lg_graph.StateGraph

        def spy_state_graph(*args: object, **kwargs: object) -> object:
            instance = original_state_graph(*args, **kwargs)
            original_compile = instance.compile

            def spy_compile(*c_args: object, **c_kwargs: object) -> object:
                captured["checkpointer"] = c_kwargs.get("checkpointer")
                return original_compile(*c_args, **c_kwargs)

            instance.compile = spy_compile  # type: ignore[method-assign]
            return instance

        # When build_support_review_graph compiles the graph through the spy
        with mock.patch.object(
            support_review_mod.lg_graph,
            "StateGraph",
            side_effect=spy_state_graph,
        ):
            support_review_mod.build_support_review_graph(checkpointer=checkpointer)

        # Then the checkpointer instance was forwarded to ``compile``
        assert captured["checkpointer"] is checkpointer
