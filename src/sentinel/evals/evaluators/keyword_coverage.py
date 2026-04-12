"""
Keyword coverage evaluator.

Check what fraction of expected keywords appear in a text field
from the case payload. Produces both a score (0-1) and a pass/fail
assertion based on a configurable threshold.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_evals import evaluators
from pydantic_evals.evaluators import evaluator

from sentinel.evals import types
from sentinel.evals.evaluators import base


def _compute_keyword_coverage(*, text: str, keywords: tuple[str, ...]) -> float:
    """
    Return the fraction of keywords found in text (case-insensitive).

    Return 1.0 when there are no keywords to check.
    """
    if not keywords:
        return 1.0
    text_lower = text.lower()
    matched = sum(1 for kw in keywords if kw.lower() in text_lower)
    return matched / len(keywords)


@dataclasses.dataclass
class KeywordCoverage(evaluators.Evaluator):
    """
    Evaluate keyword coverage in a text field from the case payload.

    Produces an assertion that passes when the coverage fraction
    meets or exceeds the configured threshold.
    """

    field_path: str = ""
    keywords: tuple[str, ...] = ()
    threshold: float = 0.5
    rubric: str = "Keyword coverage meets threshold"

    async def evaluate(
        self,
        ctx: evaluators.EvaluatorContext[types.InputData, str, Any],
    ) -> evaluators.EvaluatorOutput:
        """
        Compute keyword coverage and return a pass/fail assertion with the score as reason.
        """
        payload = ctx.inputs.case_payload
        text = str(base.resolve_field(payload=payload, field_path=self.field_path))
        coverage = _compute_keyword_coverage(text=text, keywords=self.keywords)
        passed = coverage >= self.threshold

        reason = (
            f"Keyword coverage: {coverage:.2f} "
            f"(threshold: {self.threshold:.2f}, "
            f"matched: {int(coverage * len(self.keywords))}/{len(self.keywords)})"
        )

        return {
            "keyword_coverage_pass": evaluator.EvaluationReason(
                value=passed,
                reason=reason,
            ),
        }
