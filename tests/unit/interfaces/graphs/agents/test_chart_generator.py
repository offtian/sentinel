from __future__ import annotations

from pydantic_ai import Agent

from sentinel.domain.charts import entities
from sentinel.interfaces.graphs.agents import chart_generator


class TestChartGeneratorOutput:
    def test_output_model_has_required_fields(self):
        # Given a ChartGeneratorOutput
        output = chart_generator.ChartGeneratorOutput(
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
                entities.GeneratedFile(
                    path="templates/service.yaml",
                    content="apiVersion: v1\nkind: Service",
                ),
            ),
        )

        # Then files are stored
        assert len(output.files) == 2


class TestChartGeneratorAgent:
    def test_build_agent_returns_typed_agent(self):
        # Given the agent factory

        # When building an agent
        agent = chart_generator.build_agent()

        # Then the agent is a PydanticAI Agent
        assert isinstance(agent, Agent)

    def test_output_type_is_chart_generator_output(self):
        # Given an agent built from the factory

        # When checking its output type
        agent = chart_generator.build_agent()

        # Then its output type is ChartGeneratorOutput
        assert agent._output_type is chart_generator.ChartGeneratorOutput

    def test_dependencies_dataclass_has_required_fields(self):
        # Given the Dependencies dataclass
        deps = chart_generator.Dependencies(
            service_name="api-gateway",
            image="nginx:latest",
            spec_json='{"service_name": "api-gateway"}',
            policy_json='{"team": "platform"}',
        )

        # Then fields are set
        assert deps.service_name == "api-gateway"

    def test_base_system_prompt_is_loaded(self):
        # Given the base system prompt constant

        # Then it is a non-empty string
        assert isinstance(chart_generator._PROMPT_TEMPLATE.system_text, str)
        assert len(chart_generator._PROMPT_TEMPLATE.system_text) > 50
