"""
Side-by-side comparison of two investigation backends.
"""

from __future__ import annotations

from collections.abc import Mapping

import attrs

from sentinel.domain.evaluation import metrics


@attrs.frozen
class ComparisonResult:
    """
    Compare baseline vs challenger across all evaluation dimensions.
    """

    case_id: str
    baseline: metrics.EvaluationMetrics
    challenger: metrics.EvaluationMetrics
    winner_by_dimension: Mapping[str, str]
