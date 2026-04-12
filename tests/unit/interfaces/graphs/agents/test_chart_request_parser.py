from __future__ import annotations

from pydantic_ai import Agent

from sentinel.domain.charts import entities
from sentinel.interfaces.graphs.agents import chart_request_parser


class TestChartRequestParserAgent:
    def test_build_agent_returns_typed_agent(self):
        # Given the agent factory

        # When building an agent
        agent = chart_request_parser.build_agent()

        # Then the agent is a PydanticAI Agent
        assert isinstance(agent, Agent)

    def test_output_type_is_chart_spec(self):
        # Given an agent built from the factory

        # When checking its output type
        agent = chart_request_parser.build_agent()

        # Then its output type is ChartSpec
        assert agent._output_type is entities.ChartSpec

    def test_dependencies_dataclass_has_required_fields(self):
        # Given the Dependencies dataclass
        deps = chart_request_parser.Dependencies(
            raw_message="Deploy a web service",
            requester="alice",
            team="platform",
        )

        # Then fields are set
        assert deps.raw_message == "Deploy a web service"
        assert deps.requester == "alice"
        assert deps.team == "platform"

    def test_base_system_prompt_is_loaded(self):
        # Given the base system prompt constant

        # Then it is a non-empty string
        assert isinstance(chart_request_parser._PROMPT_TEMPLATE.system_text, str)
        assert len(chart_request_parser._PROMPT_TEMPLATE.system_text) > 50
