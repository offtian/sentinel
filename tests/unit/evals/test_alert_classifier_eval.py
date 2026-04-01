"""
Tests for the AlertClassifierEvaluator.
"""

from __future__ import annotations

import pytest

from sentinel.evals.agents import alert_classifier


class TestAlertClassifierEvaluator:
    async def test_all_metrics_pass_on_correct_output(self) -> None:
        # Given a case where output matches expected exactly
        case = {
            "id": "ac-test-001",
            "output": {
                "severity": "high",
                "category": "resource_exhaustion",
                "summary": "API service OOMKilled causing 5xx errors.",
            },
            "expected": {
                "severity": "high",
                "category": "resource_exhaustion",
            },
        }
        evaluator = alert_classifier.AlertClassifierEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then all metrics pass
        assert result.passed is True
        assert result.case_id == "ac-test-001"
        assert len(result.metrics) == 3
        assert all(m.passed for m in result.metrics)

    async def test_severity_mismatch_fails(self) -> None:
        # Given a case where severity does not match
        case = {
            "id": "ac-test-002",
            "output": {
                "severity": "low",
                "category": "database",
                "summary": "Some summary.",
            },
            "expected": {
                "severity": "critical",
                "category": "database",
            },
        }
        evaluator = alert_classifier.AlertClassifierEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then severity metric fails and overall case fails
        severity_metric = next(m for m in result.metrics if m.name == "severity_accuracy")
        assert severity_metric.passed is False
        assert severity_metric.value == 0.0
        assert result.passed is False

    async def test_category_mismatch_fails(self) -> None:
        # Given a case where category does not match
        case = {
            "id": "ac-test-003",
            "output": {
                "severity": "high",
                "category": "networking",
                "summary": "Network issue detected.",
            },
            "expected": {
                "severity": "high",
                "category": "resource_exhaustion",
            },
        }
        evaluator = alert_classifier.AlertClassifierEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then category metric fails
        category_metric = next(m for m in result.metrics if m.name == "category_accuracy")
        assert category_metric.passed is False
        assert result.passed is False

    async def test_empty_summary_fails(self) -> None:
        # Given a case with empty summary
        case = {
            "id": "ac-test-004",
            "output": {
                "severity": "high",
                "category": "database",
                "summary": "",
            },
            "expected": {
                "severity": "high",
                "category": "database",
            },
        }
        evaluator = alert_classifier.AlertClassifierEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then has_summary metric fails
        summary_metric = next(m for m in result.metrics if m.name == "has_summary")
        assert summary_metric.passed is False
        assert summary_metric.value == 0.0
        assert result.passed is False

    async def test_case_insensitive_matching(self) -> None:
        # Given a case where severity and category differ only in casing
        case = {
            "id": "ac-test-005",
            "output": {
                "severity": "HIGH",
                "category": "Resource_Exhaustion",
                "summary": "Some summary text here.",
            },
            "expected": {
                "severity": "high",
                "category": "resource_exhaustion",
            },
        }
        evaluator = alert_classifier.AlertClassifierEvaluator()

        # When evaluating the case
        result = await evaluator.evaluate_case(case=case)

        # Then all metrics pass despite case differences
        assert result.passed is True

    async def test_evaluator_name(self) -> None:
        # Given an alert classifier evaluator
        evaluator = alert_classifier.AlertClassifierEvaluator()

        # Then the name is correct
        assert evaluator.name == "alert_classifier"

    async def test_run_with_full_dataset(self) -> None:
        # Given the evaluator and a small passing dataset
        evaluator = alert_classifier.AlertClassifierEvaluator()
        dataset = [
            {
                "id": "run-001",
                "output": {"severity": "high", "category": "database", "summary": "DB issue."},
                "expected": {"severity": "high", "category": "database"},
            },
            {
                "id": "run-002",
                "output": {"severity": "critical", "category": "networking", "summary": "Net down."},
                "expected": {"severity": "critical", "category": "networking"},
            },
        ]

        # When running the evaluator
        report = await evaluator.run(dataset=dataset)

        # Then both cases pass
        assert report.evaluator_name == "alert_classifier"
        assert report.pass_rate == 1.0
        assert len(report.results) == 2
