from __future__ import annotations

import attrs
import pytest

from sentinel.domain.evaluation import metrics


class TestEvaluationMetrics:
    def test_creates_metrics_with_all_dimensions(self) -> None:
        # Given a full set of evaluation dimensions
        # When EvaluationMetrics is created
        result = metrics.EvaluationMetrics(
            factual_precision=0.85,
            factual_recall=0.78,
            hallucination_rate=0.05,
            latency_p50_ms=450,
            latency_p95_ms=1200,
            latency_p99_ms=2500,
            confidence_brier_score=0.12,
            evidence_source_count=5,
            evidence_diversity=0.8,
            robustness_variance=0.03,
            degradation_score=0.95,
            token_cost=3200,
        )

        # Then all dimensions are accessible
        assert result.factual_precision == 0.85
        assert result.hallucination_rate == 0.05
        assert result.latency_p50_ms == 450
        assert result.token_cost == 3200

    def test_is_immutable(self) -> None:
        # Given an EvaluationMetrics instance
        result = metrics.EvaluationMetrics(
            factual_precision=0.85,
            factual_recall=0.78,
            hallucination_rate=0.05,
            latency_p50_ms=450,
            latency_p95_ms=1200,
            latency_p99_ms=2500,
            confidence_brier_score=0.12,
            evidence_source_count=5,
            evidence_diversity=0.8,
            robustness_variance=0.03,
            degradation_score=0.95,
            token_cost=3200,
        )

        # When attempting to mutate
        # Then it raises FrozenInstanceError
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            result.factual_precision = 0.9  # type: ignore[misc]
