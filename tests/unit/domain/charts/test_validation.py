from __future__ import annotations

import asyncio
from unittest import mock

from sentinel.domain.charts import entities, validation


class TestValidateChart:
    def test_returns_passing_result_when_both_tools_succeed(self):
        # Given a chart output with valid YAML
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api-gateway",
                ),
            ),
        )

        # When validating with mocked subprocess (both pass)
        with (
            mock.patch.object(validation, "_run_helm_template") as mock_helm,
            mock.patch.object(validation, "_run_kubeconform") as mock_conform,
        ):
            mock_helm.return_value = (True, (), ())
            mock_conform.return_value = (True, (), ())

            result = asyncio.run(validation.validate_chart(chart=chart))

        # Then both validations pass
        assert result.helm_template_ok is True
        assert result.kubeconform_ok is True
        assert result.errors == ()

    def test_returns_failing_result_when_helm_template_fails(self):
        # Given a chart output
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="invalid: yaml: [",
                ),
            ),
        )

        # When helm template fails
        with (
            mock.patch.object(validation, "_run_helm_template") as mock_helm,
            mock.patch.object(validation, "_run_kubeconform") as mock_conform,
        ):
            mock_helm.return_value = (False, ("Error: template rendering failed",), ())
            mock_conform.return_value = (True, (), ())

            result = asyncio.run(validation.validate_chart(chart=chart))

        # Then helm_template_ok is False and errors are captured
        assert result.helm_template_ok is False
        assert len(result.errors) == 1
        assert "template rendering failed" in result.errors[0]

    def test_returns_failing_result_when_kubeconform_fails(self):
        # Given a chart output
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
            ),
        )

        # When kubeconform fails
        with (
            mock.patch.object(validation, "_run_helm_template") as mock_helm,
            mock.patch.object(validation, "_run_kubeconform") as mock_conform,
        ):
            mock_helm.return_value = (True, (), ())
            mock_conform.return_value = (False, ("resource Deployment missing spec",), ())

            result = asyncio.run(validation.validate_chart(chart=chart))

        # Then kubeconform_ok is False
        assert result.kubeconform_ok is False
        assert len(result.errors) == 1

    def test_captures_warnings(self):
        # Given warnings from both tools
        chart = entities.ChartOutput(
            service_name="api-gateway",
            files=(
                entities.GeneratedFile(
                    path="templates/deployment.yaml",
                    content="apiVersion: apps/v1\nkind: Deployment",
                ),
            ),
        )

        # When both pass with warnings
        with (
            mock.patch.object(validation, "_run_helm_template") as mock_helm,
            mock.patch.object(validation, "_run_kubeconform") as mock_conform,
        ):
            mock_helm.return_value = (True, (), ("chart has no .helmignore",))
            mock_conform.return_value = (True, (), ("deprecated API version",))

            result = asyncio.run(validation.validate_chart(chart=chart))

        # Then warnings are captured
        assert result.helm_template_ok is True
        assert result.kubeconform_ok is True
        assert len(result.warnings) == 2
