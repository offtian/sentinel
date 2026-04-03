"""
Unit tests for chart generation evaluators.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from sentinel.evals import types
from sentinel.evals.evaluators import chart_evaluators


def _make_eval_context(*, case_payload: dict) -> chart_evaluators.evaluators.EvaluatorContext:
    """Build a minimal evaluator context for testing."""
    ctx = MagicMock()
    ctx.inputs = types.InputData(
        agent_name="chart_generator",
        case_payload=case_payload,
    )
    return ctx


class TestYamlStructureCheck:
    def test_passes_when_required_files_present(self):
        # Given a case with deployment and service files
        evaluator = chart_evaluators.YamlStructureCheck(
            required_file_patterns=("deployment", "service"),
            rubric="Has required Kubernetes resources",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {
                    "files": [
                        {"path": "templates/deployment.yaml", "content": "apiVersion: apps/v1"},
                        {"path": "templates/service.yaml", "content": "apiVersion: v1"},
                    ],
                },
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then it passes
        key = next(iter(result))
        assert result[key].value is True

    def test_fails_when_required_file_missing(self):
        # Given a case missing the service file
        evaluator = chart_evaluators.YamlStructureCheck(
            required_file_patterns=("deployment", "service"),
            rubric="Has required Kubernetes resources",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {
                    "files": [
                        {"path": "templates/deployment.yaml", "content": "apiVersion: apps/v1"},
                    ],
                },
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then it fails
        key = next(iter(result))
        assert result[key].value is False

    def test_fails_when_output_has_no_files(self):
        # Given a case with an empty files list
        evaluator = chart_evaluators.YamlStructureCheck(
            required_file_patterns=("deployment",),
            rubric="Has deployment file",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {"files": []},
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then it fails
        key = next(iter(result))
        assert result[key].value is False

    def test_passes_with_no_required_patterns(self):
        # Given an evaluator with no required patterns
        evaluator = chart_evaluators.YamlStructureCheck(
            required_file_patterns=(),
            rubric="No requirements",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {"files": []},
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then it passes (no missing patterns)
        key = next(iter(result))
        assert result[key].value is True

    def test_reason_includes_missing_pattern_on_failure(self):
        # Given a case missing the hpa file
        evaluator = chart_evaluators.YamlStructureCheck(
            required_file_patterns=("hpa",),
            rubric="Has HPA",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {
                    "files": [
                        {"path": "templates/deployment.yaml", "content": ""},
                    ],
                },
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then the reason mentions the missing pattern
        key = next(iter(result))
        assert "hpa" in result[key].reason


class TestSpecCoverageCheck:
    def test_passes_when_file_count_meets_minimum(self):
        # Given a case with 3 files and min_files=2
        evaluator = chart_evaluators.SpecCoverageCheck(
            min_files_field="expected.min_files",
            rubric="File count meets minimum",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {
                    "files": [
                        {"path": "a.yaml", "content": "x"},
                        {"path": "b.yaml", "content": "y"},
                        {"path": "c.yaml", "content": "z"},
                    ],
                },
                "expected": {"min_files": 2},
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then it passes
        key = next(iter(result))
        assert result[key].value is True

    def test_fails_when_file_count_below_minimum(self):
        # Given a case with 1 file and min_files=3
        evaluator = chart_evaluators.SpecCoverageCheck(
            min_files_field="expected.min_files",
            rubric="File count meets minimum",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {
                    "files": [{"path": "a.yaml", "content": "x"}],
                },
                "expected": {"min_files": 3},
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then it fails
        key = next(iter(result))
        assert result[key].value is False

    def test_passes_when_file_count_exactly_meets_minimum(self):
        # Given a case with exactly 2 files and min_files=2
        evaluator = chart_evaluators.SpecCoverageCheck(
            min_files_field="expected.min_files",
            rubric="File count meets minimum",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {
                    "files": [
                        {"path": "a.yaml", "content": "x"},
                        {"path": "b.yaml", "content": "y"},
                    ],
                },
                "expected": {"min_files": 2},
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then it passes (boundary check)
        key = next(iter(result))
        assert result[key].value is True

    def test_reason_includes_actual_and_minimum_counts(self):
        # Given a case with 1 file and min_files=4
        evaluator = chart_evaluators.SpecCoverageCheck(
            min_files_field="expected.min_files",
            rubric="File count meets minimum",
        )

        ctx = _make_eval_context(
            case_payload={
                "output": {
                    "files": [{"path": "a.yaml", "content": "x"}],
                },
                "expected": {"min_files": 4},
            },
        )

        # When evaluating
        result = asyncio.run(evaluator.evaluate(ctx))

        # Then the reason mentions both counts
        key = next(iter(result))
        assert "1" in result[key].reason
        assert "4" in result[key].reason
