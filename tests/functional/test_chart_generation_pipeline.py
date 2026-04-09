"""
End-to-end test for the chart generation pipeline.

Monkeypatches helper functions to return deterministic outputs,
then runs the full pipeline and verifies the result.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest import mock

from sentinel.domain.charts import entities, validation
from sentinel.interfaces.graphs import chart_generation
from tests.functional.conftest import _build_fake_config


_FAKE_DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway
  labels:
    app.kubernetes.io/name: api-gateway
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: api-gateway
  template:
    metadata:
      labels:
        app.kubernetes.io/name: api-gateway
    spec:
      containers:
        - name: api-gateway
          image: myrepo/api-gateway:latest
          ports:
            - containerPort: 8080
"""

_FAKE_SERVICE = """\
apiVersion: v1
kind: Service
metadata:
  name: api-gateway
spec:
  selector:
    app.kubernetes.io/name: api-gateway
  ports:
    - port: 8080
      targetPort: 8080
"""


class TestChartGenerationPipeline:
    def test_full_pipeline_with_mocked_agents(self):
        # Given a chart request
        request = entities.ChartRequest(
            requester="alice",
            team="platform",
            raw_message="Deploy api-gateway on port 8080",
            requested_at=datetime(2026, 4, 3, tzinfo=UTC),
        )

        fake_spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api-gateway:latest",
            ports=(entities.PortSpec(container_port=8080, name="http"),),
        )

        fake_files = (
            entities.GeneratedFile(
                path="templates/deployment.yaml",
                content=_FAKE_DEPLOYMENT,
            ),
            entities.GeneratedFile(
                path="templates/service.yaml",
                content=_FAKE_SERVICE,
            ),
        )

        fake_validation = entities.ValidationResult(
            helm_template_ok=True,
            kubeconform_ok=True,
        )

        fake_config = _build_fake_config({})
        fake_config.chart_parser_model = "test-model"
        fake_config.chart_generator_model = "test-model"
        fake_config.chart_max_retries = 3

        # When running the full pipeline with mocked helpers
        with (
            mock.patch.object(chart_generation, "_parse_request") as mock_parse,
            mock.patch.object(chart_generation, "_load_policy") as mock_load,
            mock.patch.object(chart_generation, "_generate_chart_files") as mock_gen,
            mock.patch.object(validation, "validate_chart") as mock_validate,
            mock.patch.object(chart_generation, "_commit_chart") as mock_commit,
        ):
            mock_parse.return_value = fake_spec
            mock_load.return_value = entities.TeamPolicy(
                team="platform",
                namespace="platform-prod",
                max_memory="2Gi",
                max_cpu="2000m",
                max_replicas=10,
                require_network_policy=True,
                require_non_root=True,
            )
            mock_gen.return_value = fake_files
            mock_validate.return_value = fake_validation
            mock_commit.return_value = "https://github.com/org/repo/pull/42"

            result = asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    config=fake_config,
                )
            )

        # Then the pipeline produces a successful result
        assert result.service_name == "api-gateway"
        assert result.files_generated == 2
        assert result.validation_passed is True
        assert result.generation_attempts == 1
        assert result.pr_url == "https://github.com/org/repo/pull/42"
        assert result.error is None
        assert result.confidence is not None
        assert result.confidence.total >= 0.7

    def test_self_heal_loop_retries_on_validation_failure(self):
        # Given a request where first generation fails but second succeeds
        request = entities.ChartRequest(
            requester="alice",
            team="platform",
            raw_message="Deploy api-gateway",
            requested_at=datetime(2026, 4, 3, tzinfo=UTC),
        )

        fake_spec = entities.ChartSpec(
            service_name="api-gateway",
            image="myrepo/api-gateway:latest",
        )

        bad_files = (
            entities.GeneratedFile(
                path="templates/deployment.yaml",
                content="invalid yaml",
            ),
        )
        good_files = (
            entities.GeneratedFile(
                path="templates/deployment.yaml",
                content=_FAKE_DEPLOYMENT,
            ),
        )

        failing_validation = entities.ValidationResult(
            helm_template_ok=False,
            kubeconform_ok=False,
            errors=("template rendering failed",),
        )
        passing_validation = entities.ValidationResult(
            helm_template_ok=True,
            kubeconform_ok=True,
        )

        fake_config = _build_fake_config({})
        fake_config.chart_parser_model = "test-model"
        fake_config.chart_generator_model = "test-model"
        fake_config.chart_max_retries = 3

        # When the first attempt fails but the second succeeds
        with (
            mock.patch.object(chart_generation, "_parse_request") as mock_parse,
            mock.patch.object(chart_generation, "_load_policy") as mock_load,
            mock.patch.object(chart_generation, "_generate_chart_files") as mock_gen,
            mock.patch.object(validation, "validate_chart") as mock_validate,
            mock.patch.object(chart_generation, "_commit_chart") as mock_commit,
        ):
            mock_parse.return_value = fake_spec
            mock_load.return_value = entities.TeamPolicy(team="platform")
            mock_gen.side_effect = [bad_files, good_files]
            mock_validate.side_effect = [failing_validation, passing_validation]
            mock_commit.return_value = "https://github.com/org/repo/pull/43"

            result = asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    config=fake_config,
                    max_retries=3,
                )
            )

        # Then the pipeline retried and succeeded
        assert result.validation_passed is True
        assert result.generation_attempts == 2
        assert result.error is None

    def test_pipeline_handles_parse_failure_gracefully(self):
        # Given a request where parsing fails
        request = entities.ChartRequest(
            requester="alice",
            team="platform",
            raw_message="gibberish that can't be parsed",
            requested_at=datetime(2026, 4, 3, tzinfo=UTC),
        )

        fake_config = _build_fake_config({})
        fake_config.chart_parser_model = "test-model"
        fake_config.chart_generator_model = "test-model"
        fake_config.chart_max_retries = 3

        # When the parser agent raises
        with mock.patch.object(chart_generation, "_parse_request") as mock_parse:
            mock_parse.side_effect = RuntimeError("LLM timeout")

            result = asyncio.run(
                chart_generation.generate_chart(
                    request=request,
                    config=fake_config,
                )
            )

        # Then error is returned gracefully
        assert result.error is not None
        assert "parse" in result.error.lower()
        assert result.service_name == "unknown"
