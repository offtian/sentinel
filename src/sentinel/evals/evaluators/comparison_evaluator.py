"""
Evaluators for K8s investigation comparison.

Three evaluators assess investigation quality:
- FindingsKeywordCoverage: expected keywords appear in text fields
- MinimumSourceCount: enough findings/sources were produced
- LatencyThreshold: investigation completed within time budget
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_evals import evaluators
from pydantic_evals.evaluators import evaluator

from sentinel.evals import types


def _resolve_field(*, payload: dict[str, Any], field_path: str) -> Any:
    """
    Traverse a nested dict using a dot-separated field path.

    :raises KeyError: if any segment is missing.
    """
    current: Any = payload
    for segment in field_path.split("."):
        current = current[segment]
    return current


@dataclasses.dataclass
class FindingsKeywordCoverage(evaluators.Evaluator):
    """
    Check what fraction of expected keywords appear in a text field.
    """

    field_path: str = ""
    keywords: tuple[str, ...] = ()
    threshold: float = 0.5

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        text = str(_resolve_field(payload=payload, field_path=self.field_path))
        text_lower = text.lower()

        if not self.keywords:
            coverage = 1.0
        else:
            matched = sum(1 for kw in self.keywords if kw.lower() in text_lower)
            coverage = matched / len(self.keywords)

        passed = coverage >= self.threshold
        reason = (
            f"Keyword coverage: {coverage:.2f} "
            f"(threshold: {self.threshold:.2f}, "
            f"matched: {int(coverage * len(self.keywords))}/{len(self.keywords)})"
        )

        name = self.get_default_evaluation_name()
        return {
            f"{name}_pass": evaluator.EvaluationReason(value=passed, reason=reason),
        }


@dataclasses.dataclass
class MinimumSourceCount(evaluators.Evaluator):
    """
    Check that the actual findings/source count meets the expected minimum.
    """

    field_path: str = "min_findings_count"
    actual_count_field: str = "actual_findings_count"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        minimum = int(_resolve_field(payload=payload, field_path=self.field_path))
        actual = int(_resolve_field(payload=payload, field_path=self.actual_count_field))
        passed = actual >= minimum
        reason = f"Source count: {actual} (minimum: {minimum})"

        name = self.get_default_evaluation_name()
        return {
            f"{name}_pass": evaluator.EvaluationReason(value=passed, reason=reason),
        }


@dataclasses.dataclass
class LatencyThreshold(evaluators.Evaluator):
    """
    Check that actual latency is within the threshold.
    """

    threshold_field: str = "max_latency_ms"
    actual_field: str = "actual_latency_ms"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        payload = ctx.inputs.case_payload
        threshold = int(_resolve_field(payload=payload, field_path=self.threshold_field))
        actual = int(_resolve_field(payload=payload, field_path=self.actual_field))
        passed = actual <= threshold
        reason = f"Latency: {actual}ms (threshold: {threshold}ms)"

        name = self.get_default_evaluation_name()
        return {
            f"{name}_pass": evaluator.EvaluationReason(value=passed, reason=reason),
        }
