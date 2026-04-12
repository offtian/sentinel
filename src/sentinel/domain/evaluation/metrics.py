"""
Pipeline-agnostic evaluation metrics for comparing investigation backends.
"""

from __future__ import annotations

import attrs


@attrs.frozen
class EvaluationMetrics:
    """
    Holistic quality metrics for a single investigation run.
    """

    factual_precision: float
    factual_recall: float
    hallucination_rate: float
    latency_p50_ms: int
    latency_p95_ms: int
    latency_p99_ms: int
    confidence_brier_score: float
    evidence_source_count: int
    evidence_diversity: float
    robustness_variance: float
    degradation_score: float
    token_cost: int
    token_cost_usd: float = 0.0
