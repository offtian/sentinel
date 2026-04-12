"""
Side-by-side comparison of two investigation backends.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import attrs

from sentinel.domain.evaluation import metrics
from sentinel.domain.sre import investigation


def _degradation_score(audit_trail: Sequence[investigation.AuditEntry]) -> float:
    """
    Return a degradation score based on the ratio of successful entries.

    Score is 1.0 if the audit trail is empty (no errors observed).

    :param audit_trail: Sequence of audit entries from an investigation run.
    :returns: Float in [0.0, 1.0] where 1.0 means no errors.
    """
    if not audit_trail:
        return 1.0
    error_count = sum(1 for entry in audit_trail if entry.error_code is not None)
    return 1.0 - (error_count / len(audit_trail))


def _evidence_diversity(sources: Sequence[str]) -> float:
    """
    Return the ratio of unique sources to total sources queried.

    Returns 0.0 if sources is empty.

    :param sources: Sequence of source names queried during an investigation.
    :returns: Float in [0.0, 1.0] representing source diversity.
    """
    if not sources:
        return 0.0
    return len(set(sources)) / len(sources)


def _metrics_from_result(result: investigation.InvestigationResult) -> metrics.EvaluationMetrics:
    """
    Derive EvaluationMetrics from a single InvestigationResult.

    Fields that require labelled ground-truth data (factual_precision,
    factual_recall, hallucination_rate, confidence_brier_score,
    robustness_variance) default to 0 / 0.0 and must be populated
    separately using an evaluator.

    :param result: The investigation result to derive metrics from.
    :returns: EvaluationMetrics populated from the result's observable data.
    """
    sources = result.sources_queried
    return metrics.EvaluationMetrics(
        factual_precision=0.0,
        factual_recall=0.0,
        hallucination_rate=0.0,
        latency_p50_ms=result.duration_ms,
        latency_p95_ms=result.duration_ms,
        latency_p99_ms=result.duration_ms,
        confidence_brier_score=0.0,
        evidence_source_count=len(sources),
        evidence_diversity=_evidence_diversity(sources),
        robustness_variance=0.0,
        degradation_score=_degradation_score(result.audit_trail),
        token_cost=0,
    )


def _pick_winner(
    *,
    baseline_val: float | int,
    challenger_val: float | int,
    baseline_name: str,
    challenger_name: str,
    lower_is_better: bool,
) -> str:
    """
    Return the name of the winner for a single dimension.

    :param baseline_val: The baseline metric value.
    :param challenger_val: The challenger metric value.
    :param baseline_name: Identifier for the baseline adapter.
    :param challenger_name: Identifier for the challenger adapter.
    :param lower_is_better: True when a smaller value is preferred.
    :returns: The name of the winning adapter, or "tie" when equal.
    """
    if baseline_val == challenger_val:
        return "tie"
    if lower_is_better:
        return baseline_name if baseline_val < challenger_val else challenger_name
    return baseline_name if baseline_val > challenger_val else challenger_name


_HIGHER_IS_BETTER = frozenset(
    {
        "evidence_source_count",
        "evidence_diversity",
        "degradation_score",
    }
)

_LOWER_IS_BETTER = frozenset(
    {
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
        "token_cost",
        "token_cost_usd",
        "hallucination_rate",
    }
)


@attrs.frozen
class ComparisonResult:
    """
    Compare baseline vs challenger across all evaluation dimensions.
    """

    case_id: str
    baseline: metrics.EvaluationMetrics
    challenger: metrics.EvaluationMetrics
    winner_by_dimension: Mapping[str, str]

    @staticmethod
    def from_investigation_results(
        *,
        baseline: investigation.InvestigationResult,
        challenger: investigation.InvestigationResult,
        case_id: str = "",
    ) -> ComparisonResult:
        """
        Build a ComparisonResult directly from two InvestigationResult objects.

        Derives EvaluationMetrics for each result and computes per-dimension
        winners.  Only non-zero dimensions are included in ``winner_by_dimension``
        to avoid noise from fields that require labelled data.

        :param baseline: The baseline investigation result.
        :param challenger: The challenger investigation result.
        :param case_id: Optional identifier for the test case.
        :returns: A fully populated ComparisonResult.
        """
        baseline_metrics = _metrics_from_result(baseline)
        challenger_metrics = _metrics_from_result(challenger)

        winner_by_dimension: dict[str, str] = {}

        for dimension in _HIGHER_IS_BETTER:
            baseline_val = getattr(baseline_metrics, dimension)
            challenger_val = getattr(challenger_metrics, dimension)
            if baseline_val == 0 and challenger_val == 0:
                continue
            winner_by_dimension[dimension] = _pick_winner(
                baseline_val=baseline_val,
                challenger_val=challenger_val,
                baseline_name=baseline.adapter_name,
                challenger_name=challenger.adapter_name,
                lower_is_better=False,
            )

        for dimension in _LOWER_IS_BETTER:
            baseline_val = getattr(baseline_metrics, dimension)
            challenger_val = getattr(challenger_metrics, dimension)
            if baseline_val == 0 and challenger_val == 0:
                continue
            winner_by_dimension[dimension] = _pick_winner(
                baseline_val=baseline_val,
                challenger_val=challenger_val,
                baseline_name=baseline.adapter_name,
                challenger_name=challenger.adapter_name,
                lower_is_better=True,
            )

        return ComparisonResult(
            case_id=case_id,
            baseline=baseline_metrics,
            challenger=challenger_metrics,
            winner_by_dimension=winner_by_dimension,
        )
