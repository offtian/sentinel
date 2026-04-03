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
    def test_agent_exists_and_is_typed_correctly(self):
        # Given the agent module

        # Then the agent is a PydanticAI Agent
        assert isinstance(chart_generator.agent, Agent)

    def test_output_type_is_chart_generator_output(self):
        # Given the agent

        # Then its output type is ChartGeneratorOutput
        assert chart_generator.agent._output_type is chart_generator.ChartGeneratorOutput

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

    def test_system_prompt_is_loaded(self):
        # Given the system prompt

        # Then it is a non-empty string
        assert isinstance(chart_generator.SYSTEM_PROMPT, str)
        assert len(chart_generator.SYSTEM_PROMPT) > 50
