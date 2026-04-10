from __future__ import annotations

import asyncio
from unittest import mock

from sentinel.domain.charts import validation
from sentinel.interfaces.graphs import chart_generation
from tests import factories
from tests.functional.conftest import _build_fake_config


class TestGenerateChart:
    def test_full_pipeline_success(self):
        # Given a chart request and mocked agents
        request = factories.make_chart_request()
        spec = factories.make_chart_spec()
        policy = factories.make_team_policy()
        generated_files = (
            factories.make_generated_file(
                path="templates/deployment.yaml",
                content="apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api-gateway",
            ),
            factories.make_generated_file(
                path="templates/service.yaml",
                content="apiVersion: v1\nkind: Service\nmetadata:\n  name: api-gateway",
            ),
        )
        validation_result = factories.make_validation_result()

        fake_config = _build_fake_config({})
        fake_config.chart_parser_model = "test-model"
        fake_config.chart_generator_model = "test-model"
        fake_config.chart_max_retries = 3

        # When running the pipeline with all steps mocked
        with (
            mock.patch.object(chart_generation, "_parse_request") as mock_parse,
            mock.patch.object(chart_generation, "_load_policy") as mock_load,
            mock.patch.object(validation, "validate_chart") as mock_validate,
            mock.patch.object(chart_generation, "_generate_chart_files") as mock_gen,
            mock.patch.object(chart_generation, "_commit_chart") as mock_commit,
        ):
            mock_parse.return_value = spec
            mock_load.return_value = policy
            mock_gen.return_value = generated_files
            mock_validate.return_value = validation_result
            mock_commit.return_value = "https://github.com/org/repo/pull/42"

            result = asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    agent_for=fake_config.agent_for,
                )
            )

        # Then the pipeline succeeds
        assert result.service_name == "api-gateway"
        assert result.validation_passed is True
        assert result.error is None

    def test_pipeline_returns_error_when_policy_not_found(self):
        # Given a request for an unknown team
        request = factories.make_chart_request(team="nonexistent")
        spec = factories.make_chart_spec()

        fake_config = _build_fake_config({})
        fake_config.chart_parser_model = "test-model"
        fake_config.chart_generator_model = "test-model"
        fake_config.chart_max_retries = 3

        # When the policy load fails
        with (
            mock.patch.object(chart_generation, "_parse_request") as mock_parse,
            mock.patch.object(chart_generation, "_load_policy") as mock_load,
        ):
            mock_parse.return_value = spec
            mock_load.side_effect = FileNotFoundError("No policy file")

            result = asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    agent_for=fake_config.agent_for,
                )
            )

        # Then error is returned
        assert result.error is not None
        assert "policy" in result.error.lower() or "Policy" in result.error
