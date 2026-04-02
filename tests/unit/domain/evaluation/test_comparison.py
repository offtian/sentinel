from __future__ import annotations

from sentinel.domain.evaluation import comparison, metrics


def _make_metrics(
    *, precision: float = 0.8, latency: int = 500
) -> metrics.EvaluationMetrics:
    return metrics.EvaluationMetrics(
        factual_precision=precision,
        factual_recall=0.7,
        hallucination_rate=0.05,
        latency_p50_ms=latency,
        latency_p95_ms=latency * 2,
        latency_p99_ms=latency * 4,
        confidence_brier_score=0.1,
        evidence_source_count=3,
        evidence_diversity=0.6,
        robustness_variance=0.02,
        degradation_score=0.9,
        token_cost=2000,
    )


class TestComparisonResult:
    def test_creates_comparison_with_winner_by_dimension(self) -> None:
        # Given metrics for two adapters
        baseline = _make_metrics(precision=0.85, latency=600)
        challenger = _make_metrics(precision=0.78, latency=350)

        # When a ComparisonResult is created
        result = comparison.ComparisonResult(
            case_id="k8s-crashloop-001",
            baseline=baseline,
            challenger=challenger,
            winner_by_dimension={
                "factual_precision": "native_k8s",
                "latency_p50_ms": "kagent",
            },
        )

        # Then winners are accessible per dimension
        assert result.winner_by_dimension["factual_precision"] == "native_k8s"
        assert result.winner_by_dimension["latency_p50_ms"] == "kagent"
