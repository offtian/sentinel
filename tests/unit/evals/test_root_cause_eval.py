"""
Tests for the root cause analyser evaluators via the pydantic_evals pattern.
"""

from __future__ import annotations

from typing import Any

import pytest

from sentinel.evals import types
from sentinel.evals.evaluators import keyword_coverage, structural


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
        agent_name="root_cause_analyser",
        case_payload=case_payload,
    )
    ctx.output = ""
    return ctx


def _make_passing_payload(*, case_id: str = "rca-test-001") -> dict[str, Any]:
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


class TestKeywordCoverageEvaluator:
    async def test_passes_when_all_keywords_present(self) -> None:
        # Given a case where all expected keywords are in the root cause text
        payload = _make_passing_payload()
        ctx = _make_context(case_payload=payload)
        evaluator = keyword_coverage.KeywordCoverage(
            field_path="output.root_cause",
            keywords=("OOMKill", "memory"),
            threshold=0.5,
            rubric="Root cause covers keywords",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is True

    async def test_fails_when_no_keywords_match(self) -> None:
        # Given a case where root_cause text misses all expected keywords
        payload = {
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
        ctx = _make_context(case_payload=payload)
        evaluator = keyword_coverage.KeywordCoverage(
            field_path="output.root_cause",
            keywords=("OOMKill", "memory", "api-service", "pod"),
            threshold=0.5,
            rubric="Root cause covers keywords",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is False

    async def test_partial_coverage_below_threshold(self) -> None:
        # Given a case where only 1 of 4 keywords matches
        payload = {
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
        ctx = _make_context(case_payload=payload)
        evaluator = keyword_coverage.KeywordCoverage(
            field_path="output.root_cause",
            keywords=("memory", "OOMKill", "pod", "api-service"),
            threshold=0.5,
            rubric="Root cause covers keywords",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails (0.25 < 0.5)
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is False
        assert "0.25" in result[assertion_key].reason

    async def test_passes_with_empty_keywords(self) -> None:
        # Given a case with no expected keywords
        payload = _make_passing_payload()
        ctx = _make_context(case_payload=payload)
        evaluator = keyword_coverage.KeywordCoverage(
            field_path="output.root_cause",
            keywords=(),
            threshold=0.5,
            rubric="Root cause covers keywords",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes (vacuous truth)
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is True


class TestRemediationCheck:
    async def test_passes_when_steps_exist(self) -> None:
        # Given a case with non-empty remediation steps
        payload = _make_passing_payload()
        ctx = _make_context(case_payload=payload)
        evaluator = structural.StructuralCheck(
            field_path="output.remediation_steps",
            check_type="has_items",
            rubric="Remediation steps are non-empty",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is True

    async def test_fails_when_steps_empty(self) -> None:
        # Given a case with empty remediation steps
        payload = _make_passing_payload()
        payload = {**payload, "output": {**payload["output"], "remediation_steps": []}}
        ctx = _make_context(case_payload=payload)
        evaluator = structural.StructuralCheck(
            field_path="output.remediation_steps",
            check_type="has_items",
            rubric="Remediation steps are non-empty",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is False


class TestEvidenceCheck:
    async def test_passes_when_evidence_exists(self) -> None:
        # Given a case with evidence items
        payload = _make_passing_payload()
        ctx = _make_context(case_payload=payload)
        evaluator = structural.StructuralCheck(
            field_path="output.evidence",
            check_type="has_items",
            rubric="Evidence is non-empty",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is True

    async def test_fails_when_evidence_empty(self) -> None:
        # Given a case with no evidence
        payload = _make_passing_payload()
        payload = {**payload, "output": {**payload["output"], "evidence": []}}
        ctx = _make_context(case_payload=payload)
        evaluator = structural.StructuralCheck(
            field_path="output.evidence",
            check_type="has_items",
            rubric="Evidence is non-empty",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is False


class TestConfidenceCheck:
    async def test_passes_when_confidence_above_minimum(self) -> None:
        # Given a case where confidence exceeds minimum
        payload = _make_passing_payload()
        ctx = _make_context(case_payload=payload)
        evaluator = structural.StructuralCheck(
            field_path="output.confidence",
            expected_field_path="expected.min_confidence",
            check_type="gte",
            rubric="Confidence above minimum",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion passes
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is True

    async def test_fails_when_confidence_below_minimum(self) -> None:
        # Given a case where confidence is below minimum
        payload = _make_passing_payload()
        payload = {**payload, "output": {**payload["output"], "confidence": 0.3}}
        ctx = _make_context(case_payload=payload)
        evaluator = structural.StructuralCheck(
            field_path="output.confidence",
            expected_field_path="expected.min_confidence",
            check_type="gte",
            rubric="Confidence above minimum",
        )

        # When evaluating
        result = await evaluator.evaluate(ctx)

        # Then the assertion fails
        assertion_key = next(k for k in result if k.endswith("_pass"))
        assert result[assertion_key].value is False


class TestRootCauseCaseLoading:
    def test_loads_root_cause_dataset(self) -> None:
        # Given the root_cause_analyser agent name
        from sentinel.evals import cases

        # When loading cases
        dataset = cases.load_cases(agent_name="root_cause_analyser")

        # Then cases are loaded from the JSON file
        assert len(dataset.cases) == 5

    def test_each_case_has_six_evaluators(self) -> None:
        # Given the loaded root_cause_analyser dataset
        from sentinel.evals import cases

        dataset = cases.load_cases(agent_name="root_cause_analyser")

        # Then each case has six evaluators (faithfulness, keyword, evidence,
        # completeness, confidence, hallucination)
        for case in dataset.cases:
            assert len(case.evaluators) == 6

    def test_raises_for_unknown_agent(self) -> None:
        # Given an unknown agent name
        from sentinel.evals import cases

        # When loading cases
        # Then a ValueError is raised
        with pytest.raises(ValueError, match="Unknown agent name"):
            cases.load_cases(agent_name="nonexistent")
