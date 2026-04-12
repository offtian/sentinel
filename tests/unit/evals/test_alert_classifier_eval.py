"""
Tests for the alert classifier evaluators via the pydantic_evals pattern.
"""

from __future__ import annotations

from typing import Any

from sentinel.evals import types
from sentinel.evals.evaluators import structural


def _make_context(
    *,
    case_payload: dict[str, Any],
) -> Any:
    """
    Build a minimal mock EvaluatorContext with the given case_payload.
    """
    from unittest import mock

    ctx = mock.MagicMock()
    ctx.inputs = types.InputData(
        agent_name="alert_classifier",
        case_payload=case_payload,
    )
    ctx.output = ""
    return ctx


class TestAlertClassifierSeverityCheck:
    async def test_passes_on_exact_match(self) -> None:
        # Given a case where severity matches exactly
        ctx = _make_context(
            case_payload={
                "id": "ac-test-001",
                "output": {
                    "severity": "high",
                    "category": "resource_exhaustion",
                    "summary": "Some summary.",
                },
                "expected": {"severity": "high", "category": "resource_exhaustion"},
            }
        )
        evaluator = structural.StructuralCheck(
            field_path="output.severity",
            expected_field_path="expected.severity",
            check_type="exact_match",
            rubric="Severity matches",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is True

    async def test_fails_on_mismatch(self) -> None:
        # Given a case where severity does not match
        ctx = _make_context(
            case_payload={
                "id": "ac-test-002",
                "output": {"severity": "low", "category": "database", "summary": "Some summary."},
                "expected": {"severity": "critical", "category": "database"},
            }
        )
        evaluator = structural.StructuralCheck(
            field_path="output.severity",
            expected_field_path="expected.severity",
            check_type="exact_match",
            rubric="Severity matches",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is False

    async def test_case_insensitive_matching(self) -> None:
        # Given a case where severity differs only in casing
        ctx = _make_context(
            case_payload={
                "id": "ac-test-005",
                "output": {
                    "severity": "HIGH",
                    "category": "Resource_Exhaustion",
                    "summary": "Some text.",
                },
                "expected": {"severity": "high", "category": "resource_exhaustion"},
            }
        )
        evaluator = structural.StructuralCheck(
            field_path="output.severity",
            expected_field_path="expected.severity",
            check_type="exact_match",
            rubric="Severity matches",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes despite case differences
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is True


class TestAlertClassifierCategoryCheck:
    async def test_passes_on_match(self) -> None:
        # Given a case where category matches
        ctx = _make_context(
            case_payload={
                "id": "ac-test-001",
                "output": {"severity": "high", "category": "resource_exhaustion", "summary": "X"},
                "expected": {"severity": "high", "category": "resource_exhaustion"},
            }
        )
        evaluator = structural.StructuralCheck(
            field_path="output.category",
            expected_field_path="expected.category",
            check_type="exact_match",
            rubric="Category matches",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is True

    async def test_fails_on_mismatch(self) -> None:
        # Given a case where category does not match
        ctx = _make_context(
            case_payload={
                "id": "ac-test-003",
                "output": {"severity": "high", "category": "networking", "summary": "Net issue."},
                "expected": {"severity": "high", "category": "resource_exhaustion"},
            }
        )
        evaluator = structural.StructuralCheck(
            field_path="output.category",
            expected_field_path="expected.category",
            check_type="exact_match",
            rubric="Category matches",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is False


class TestAlertClassifierSummaryCheck:
    async def test_passes_on_non_empty_summary(self) -> None:
        # Given a case with a non-empty summary
        ctx = _make_context(
            case_payload={
                "id": "ac-test-001",
                "output": {"severity": "high", "category": "database", "summary": "DB issue."},
                "expected": {"severity": "high", "category": "database"},
            }
        )
        evaluator = structural.StructuralCheck(
            field_path="output.summary",
            check_type="non_empty",
            rubric="Summary is non-empty",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is True

    async def test_fails_on_empty_summary(self) -> None:
        # Given a case with an empty summary
        ctx = _make_context(
            case_payload={
                "id": "ac-test-004",
                "output": {"severity": "high", "category": "database", "summary": ""},
                "expected": {"severity": "high", "category": "database"},
            }
        )
        evaluator = structural.StructuralCheck(
            field_path="output.summary",
            check_type="non_empty",
            rubric="Summary is non-empty",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is False


class TestAlertClassifierCaseLoading:
    def test_loads_alert_classifier_dataset(self) -> None:
        # Given the alert_classifier agent name
        from sentinel.evals import cases

        # When loading cases
        dataset = cases.load_cases(agent_name="alert_classifier")

        # Then cases are loaded from the JSON file
        assert len(dataset.cases) == 5

    def test_each_case_has_four_evaluators(self) -> None:
        # Given the loaded alert_classifier dataset
        from sentinel.evals import cases

        dataset = cases.load_cases(agent_name="alert_classifier")

        # Then each case has four evaluators (severity, category, summary, generic_phrase)
        for case in dataset.cases:
            assert len(case.evaluators) == 4
