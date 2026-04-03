"""
Confidence scoring for generated Helm charts.

Weighted multi-factor scoring based on validation results,
policy compliance, spec coverage, and retry count.

Factors and weights (from design spec):
- Schema validity:    0.30  (kubeconform pass/fail)
- Template rendering: 0.20  (helm template pass/warnings/fail)
- Policy compliance:  0.25  (no violations / auto-resolved / escalated)
- Spec coverage:      0.15  (fraction of requested resources generated)
- Retry count:        0.10  (0=1.0, 1=0.7, 2=0.4, 3=0.1)
"""

from __future__ import annotations

from sentinel.domain.confidence import entities as confidence_entities


_RETRY_SCORES: dict[int, float] = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.1}

_WEIGHT_SCHEMA = 0.30
_WEIGHT_TEMPLATE = 0.20
_WEIGHT_POLICY = 0.25
_WEIGHT_COVERAGE = 0.15
_WEIGHT_RETRY = 0.10


def calculate_chart_confidence(
    *,
    schema_valid: bool,
    template_renders: bool,
    template_has_warnings: bool,
    policy_compliant: bool,
    policy_auto_resolved: bool,
    spec_coverage: float,
    retry_count: int,
) -> confidence_entities.ConfidenceScore:
    """
    Calculate a weighted confidence score for a generated chart.

    :param schema_valid: True if kubeconform passed.
    :param template_renders: True if helm template succeeded.
    :param template_has_warnings: True if helm template had warnings.
    :param policy_compliant: True if no policy violations.
    :param policy_auto_resolved: True if violations were auto-resolved.
    :param spec_coverage: 0.0-1.0 fraction of requested resources generated.
    :param retry_count: Number of self-heal retries (0-3).
    :returns: A ConfidenceScore with weighted components.
    """
    schema_raw = 1.0 if schema_valid else 0.0

    if template_renders and not template_has_warnings:
        template_raw = 1.0
    elif template_renders and template_has_warnings:
        template_raw = 0.5
    else:
        template_raw = 0.0

    if policy_compliant:
        policy_raw = 1.0
    elif policy_auto_resolved:
        policy_raw = 0.7
    else:
        policy_raw = 0.0

    coverage_raw = max(0.0, min(spec_coverage, 1.0))
    retry_raw = _RETRY_SCORES.get(min(retry_count, 3), 0.1)

    total = (
        schema_raw * _WEIGHT_SCHEMA
        + template_raw * _WEIGHT_TEMPLATE
        + policy_raw * _WEIGHT_POLICY
        + coverage_raw * _WEIGHT_COVERAGE
        + retry_raw * _WEIGHT_RETRY
    )
    total = round(total, 4)

    # Pass our pre-calculated total through the relevance parameter,
    # zeroing out source and recency weights so the result equals our total.
    return confidence_entities.ConfidenceScore.from_factors(
        source_count=int(schema_raw + template_raw + policy_raw),
        max_expected_sources=3,
        relevance=total,
        recency=1.0,
        source_weight=0.0,
        relevance_weight=1.0,
        recency_weight=0.0,
    )
