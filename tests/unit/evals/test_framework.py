"""
Tests for the core eval framework: MetricResult, EvalCaseResult, EvalReport,
and BaseEvaluator.run().
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from sentinel.evals import framework


class TestMetricResult:
    def test_stores_metric_attributes(self) -> None:
        # Given a metric result with known values
        metric = framework.MetricResult(
            name="accuracy",
            value=0.95,
            threshold=0.9,
            passed=True,
        )

        # Then all attributes are accessible
        assert metric.name == "accuracy"
        assert metric.value == 0.95
        assert metric.threshold == 0.9
        assert metric.passed is True

    def test_is_frozen(self) -> None:
        # Given a metric result
        metric = framework.MetricResult(
            name="accuracy",
            value=0.95,
            threshold=0.9,
            passed=True,
        )

        # Then mutation raises an error
        with pytest.raises(AttributeError):
            metric.value = 0.5  # type: ignore[misc]


class TestMakeMetric:
    def test_passes_when_value_meets_threshold(self) -> None:
        # Given a value that meets the threshold
        # When creating a metric
        metric = framework.make_metric(name="acc", value=0.9, threshold=0.9)

        # Then the metric passes
        assert metric.passed is True

    def test_passes_when_value_exceeds_threshold(self) -> None:
        # Given a value above the threshold
        # When creating a metric
        metric = framework.make_metric(name="acc", value=0.95, threshold=0.9)

        # Then the metric passes
        assert metric.passed is True

    def test_fails_when_value_below_threshold(self) -> None:
        # Given a value below the threshold
        # When creating a metric
        metric = framework.make_metric(name="acc", value=0.5, threshold=0.9)

        # Then the metric fails
        assert metric.passed is False


class TestEvalCaseResult:
    def test_stores_case_attributes(self) -> None:
        # Given an eval case result
        metrics = (
            framework.MetricResult(name="m1", value=1.0, threshold=0.5, passed=True),
        )
        result = framework.EvalCaseResult(
            case_id="case-1",
            metrics=metrics,
            passed=True,
        )

        # Then attributes are accessible
        assert result.case_id == "case-1"
        assert len(result.metrics) == 1
        assert result.passed is True


class TestComputePassRate:
    def test_returns_zero_for_empty_results(self) -> None:
        # Given no results
        # When computing pass rate
        rate = framework.compute_pass_rate(results=())

        # Then the rate is 0.0
        assert rate == 0.0

    def test_returns_one_when_all_pass(self) -> None:
        # Given all passing results
        results = (
            framework.EvalCaseResult(case_id="c1", metrics=(), passed=True),
            framework.EvalCaseResult(case_id="c2", metrics=(), passed=True),
        )

        # When computing pass rate
        rate = framework.compute_pass_rate(results=results)

        # Then rate is 1.0
        assert rate == 1.0

    def test_returns_fraction_for_mixed_results(self) -> None:
        # Given mixed pass/fail results
        results = (
            framework.EvalCaseResult(case_id="c1", metrics=(), passed=True),
            framework.EvalCaseResult(case_id="c2", metrics=(), passed=False),
            framework.EvalCaseResult(case_id="c3", metrics=(), passed=True),
        )

        # When computing pass rate
        rate = framework.compute_pass_rate(results=results)

        # Then rate reflects the fraction
        assert rate == pytest.approx(2.0 / 3.0)


class _StubEvaluator(framework.BaseEvaluator):
    """Stub evaluator that returns a fixed metric for each case."""

    @property
    def name(self) -> str:
        return "stub"

    async def evaluate_case(self, *, case: dict) -> framework.EvalCaseResult:
        passed = case.get("should_pass", True)
        return framework.EvalCaseResult(
            case_id=case["id"],
            metrics=(
                framework.MetricResult(
                    name="stub_metric",
                    value=1.0 if passed else 0.0,
                    threshold=0.5,
                    passed=passed,
                ),
            ),
            passed=passed,
        )


class TestBaseEvaluatorRun:
    async def test_produces_report_from_dataset(self) -> None:
        # Given a stub evaluator and a two-case dataset
        evaluator = _StubEvaluator()
        dataset: Sequence[dict] = [
            {"id": "c1", "should_pass": True},
            {"id": "c2", "should_pass": True},
        ]

        # When running the evaluator
        report = await evaluator.run(dataset=dataset)

        # Then the report contains results for both cases
        assert report.evaluator_name == "stub"
        assert len(report.results) == 2
        assert report.pass_rate == 1.0

    async def test_computes_correct_pass_rate_with_failures(self) -> None:
        # Given a stub evaluator with one passing and one failing case
        evaluator = _StubEvaluator()
        dataset: Sequence[dict] = [
            {"id": "c1", "should_pass": True},
            {"id": "c2", "should_pass": False},
        ]

        # When running the evaluator
        report = await evaluator.run(dataset=dataset)

        # Then pass rate is 50%
        assert report.pass_rate == pytest.approx(0.5)

    async def test_handles_empty_dataset(self) -> None:
        # Given a stub evaluator and an empty dataset
        evaluator = _StubEvaluator()

        # When running the evaluator
        report = await evaluator.run(dataset=[])

        # Then the report is empty with 0.0 pass rate
        assert len(report.results) == 0
        assert report.pass_rate == 0.0
