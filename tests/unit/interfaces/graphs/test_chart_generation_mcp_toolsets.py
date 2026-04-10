"""
Unit tests for shared MCP toolset wiring in the chart generation pipeline.

Covers:
- chart_generator agent receives shared MCP toolsets when provided.
"""

from __future__ import annotations

import asyncio
from unittest import mock

from sentinel.domain.charts import validation
from sentinel.interfaces.graphs import chart_generation
from tests import factories


class TestChartGeneratorToolsets:
    def test_chart_generator_run_includes_shared_mcp_toolsets(self) -> None:
        # Given a chart request and shared MCP toolsets
        request = factories.make_chart_request()
        spec = factories.make_chart_spec()
        policy = factories.make_team_policy()
        generated_files = (
            factories.make_generated_file(
                path="templates/deployment.yaml",
                content="apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api-gateway",
            ),
        )
        validation_result = factories.make_validation_result()
        shared_toolset = mock.Mock(name="datadog-mcp")

        # And a fake chart_generator agent accessible via agent_for
        fake_output = mock.Mock()
        fake_output.output.files = generated_files
        fake_agent = mock.Mock()
        fake_agent.run = mock.AsyncMock(return_value=fake_output)

        def fake_agent_for(name: str) -> mock.Mock:
            return fake_agent

        # When running the pipeline with chart_generator_toolsets
        with (
            mock.patch.object(chart_generation, "_parse_request") as mock_parse,
            mock.patch.object(chart_generation, "_load_policy") as mock_load,
            mock.patch.object(validation, "validate_chart") as mock_validate,
            mock.patch.object(chart_generation, "_commit_chart") as mock_commit,
        ):
            mock_parse.return_value = spec
            mock_load.return_value = policy
            mock_validate.return_value = validation_result
            mock_commit.return_value = "https://github.com/org/repo/pull/42"

            asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    agent_for=fake_agent_for,
                    chart_generator_toolsets=(shared_toolset,),
                )
            )

            # Then the chart generator agent received the shared toolsets
            call_kwargs = fake_agent.run.call_args.kwargs
            assert call_kwargs.get("toolsets") == [shared_toolset]
