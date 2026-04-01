"""
Tests for the RootCauseAnalyserEvaluator.
"""

from __future__ import annotations

import pytest

from sentinel.evals.agents import root_cause_analyser


def _make_passing_case(*, case_id: str = "rca-test-001") -> dict:
    return {
        "id": case_id,
        "output": {
            "root_cause": "The api-service pod was OOMKilled due to memory exhaustion.",
            "confidence": 0.85,
            "evidence": ["Pod OOMKilled at 14:30 UTC", "Memory spike observed"],
            "remediation_steps": ["Increase memory limits", "Fix memory leak"],
            "affected_services": ["api-service"],
            "timeline": "14:20 memory ramp, 14:30 OOMKill",
        },
        "expected": {
            "root_cause_keywords": ["OOMKill", "memory"],
            "min_confidence": 0.6,
        },
    }


class TestRootCauseAnalyserEvaluator:
    async def test_all_metrics_pass_on_good_output(self) -> None:
        # Given a case where output matches all expected criteria
        case = _make_passing_case()
        evaluator = root_cause_analyser.RootCauseAnalyserEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then all metrics pass
        assert result.passed is True
        assert len(result.metrics) == 4
        assert all(m.passed for m in result.metrics)

    async def test_low_keyword_coverage_fails(self) -> None:
        # Given a case with root_cause missing expected keywords
        case = {
            "id": "rca-test-002",
            "output": {
                "root_cause": "The service experienced some issues.",
                "confidence": 0.8,
                "evidence": ["Some evidence"],
                "remediation_steps": ["Fix something"],
                "affected_services": ["some-service"],
                "timeline": "Something happened",
            },
            "expected": {
                "root_cause_keywords": ["OOMKill", "memory", "api-service", "pod"],
                "min_confidence": 0.5,
            },
        }
        evaluator = root_cause_analyser.RootCauseAnalyserEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then keyword_coverage metric fails
        kw_metric = next(m for m in result.metrics if m.name == "keyword_coverage")
        assert kw_metric.passed is False
        assert kw_metric.value == 0.0

    async def test_empty_remediation_fails(self) -> None:
        # Given a case with empty remediation steps
        case = _make_passing_case()
        case = {**case, "output": {**case["output"], "remediation_steps": []}}
        evaluator = root_cause_analyser.RootCauseAnalyserEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then has_remediation metric fails
        rem_metric = next(m for m in result.metrics if m.name == "has_remediation")
        assert rem_metric.passed is False
        assert result.passed is False

    async def test_empty_evidence_fails(self) -> None:
        # Given a case with no evidence
        case = _make_passing_case()
        case = {**case, "output": {**case["output"], "evidence": []}}
        evaluator = root_cause_analyser.RootCauseAnalyserEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then has_evidence metric fails
        ev_metric = next(m for m in result.metrics if m.name == "has_evidence")
        assert ev_metric.passed is False
        assert result.passed is False

    async def test_confidence_below_minimum_fails(self) -> None:
        # Given a case with confidence below the expected minimum
        case = _make_passing_case()
        case = {**case, "output": {**case["output"], "confidence": 0.3}}
        evaluator = root_cause_analyser.RootCauseAnalyserEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then confidence_above_minimum metric fails
        conf_metric = next(m for m in result.metrics if m.name == "confidence_above_minimum")
        assert conf_metric.passed is False
        assert result.passed is False

    async def test_keyword_coverage_is_partial(self) -> None:
        # Given a case where only some keywords match
        case = {
            "id": "rca-test-006",
            "output": {
                "root_cause": "Memory exhaustion caused the failure.",
                "confidence": 0.7,
                "evidence": ["Evidence item"],
                "remediation_steps": ["Step 1"],
                "affected_services": ["svc"],
                "timeline": "Timeline",
            },
            "expected": {
                "root_cause_keywords": ["memory", "OOMKill", "pod", "api-service"],
                "min_confidence": 0.5,
            },
        }
        evaluator = root_cause_analyser.RootCauseAnalyserEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then keyword coverage is 0.25 (1 of 4) which is below 0.5 threshold
        kw_metric = next(m for m in result.metrics if m.name == "keyword_coverage")
        assert kw_metric.value == pytest.approx(0.25)
        assert kw_metric.passed is False

    async def test_evaluator_name(self) -> None:
        # Given a root cause analyser evaluator
        evaluator = root_cause_analyser.RootCauseAnalyserEvaluator()

        # Then the name is correct
        assert evaluator.name == "root_cause_analyser"

    async def test_run_produces_report(self) -> None:
        # Given the evaluator and a dataset of two passing cases
        evaluator = root_cause_analyser.RootCauseAnalyserEvaluator()
        dataset = [
            _make_passing_case(case_id="run-001"),
            _make_passing_case(case_id="run-002"),
        ]

        # When running the evaluator
        report = await evaluator.run(dataset=dataset)

        # Then both cases pass
        assert report.pass_rate == 1.0
        assert len(report.results) == 2
