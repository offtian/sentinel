from __future__ import annotations

from pydantic_ai import Agent

from sentinel.domain.charts import entities
from sentinel.interfaces.graphs.agents import chart_request_parser


class TestChartRequestParserAgent:
    def test_agent_exists_and_is_typed_correctly(self):
        # Given the agent module

        # Then the agent is a PydanticAI Agent
        assert isinstance(chart_request_parser.agent, Agent)

    def test_output_type_is_chart_spec(self):
        # Given the agent

        # Then its output type is ChartSpec
        assert chart_request_parser.agent._output_type is entities.ChartSpec

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

    def test_system_prompt_is_loaded(self):
        # Given the system prompt

        # Then it is a non-empty string
        assert isinstance(chart_request_parser.SYSTEM_PROMPT, str)
        assert len(chart_request_parser.SYSTEM_PROMPT) > 50
