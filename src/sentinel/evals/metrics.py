"""
Scoring model and per-agent metric taxonomy.

Define weighted metric specifications per agent and compute
composite quality scores from evaluator assertion results.
"""

from __future__ import annotations

import attrs


@attrs.frozen
class MetricWeight:
    """
    A single named metric with its weight and evaluator key mapping.
    """

    name: str
    weight: float
    evaluator_key: str


@attrs.frozen
class AgentMetricSpec:
    """
    Weighted metric specification for a single agent type.
    """

    agent_name: str
    metrics: tuple[MetricWeight, ...]


def compute_composite_score(
    *,
    spec: AgentMetricSpec,
    assertion_results: dict[str, bool | float],
) -> float:
    """
    Compute a weighted composite score from assertion results.

    For each metric in the spec, look up the corresponding assertion result.
    Missing keys are skipped and their weight is redistributed proportionally.

    :returns: Score clamped to [0.0, 1.0], rounded to 4 decimal places.
    """
    present_metrics: list[tuple[MetricWeight, float]] = []

    for metric in spec.metrics:
        if metric.evaluator_key in assertion_results:
            raw = assertion_results[metric.evaluator_key]
            value = float(raw) if not isinstance(raw, bool) else (1.0 if raw else 0.0)
            present_metrics.append((metric, value))

    if not present_metrics:
        return 0.0

    total_weight = sum(m.weight for m, _ in present_metrics)
    if total_weight == 0:
        return 0.0

    weighted_sum = sum((m.weight / total_weight) * v for m, v in present_metrics)
    return round(max(0.0, min(weighted_sum, 1.0)), 4)


AGENT_METRIC_SPECS: dict[str, AgentMetricSpec] = {
    "alert_classifier": AgentMetricSpec(
        agent_name="alert_classifier",
        metrics=(
            MetricWeight(
                name="severity_accuracy", weight=0.35, evaluator_key="severity_exact_match_pass"
            ),
            MetricWeight(
                name="category_accuracy", weight=0.35, evaluator_key="category_exact_match_pass"
            ),
            MetricWeight(
                name="summary_quality", weight=0.15, evaluator_key="summary_non_empty_pass"
            ),
            MetricWeight(
                name="no_hallucination", weight=0.15, evaluator_key="generic_phrase_pass"
            ),
        ),
    ),
    "root_cause_analyser": AgentMetricSpec(
        agent_name="root_cause_analyser",
        metrics=(
            MetricWeight(name="faithfulness", weight=0.25, evaluator_key="faithfulness_pass"),
            MetricWeight(
                name="keyword_coverage", weight=0.20, evaluator_key="keyword_coverage_pass"
            ),
            MetricWeight(
                name="evidence_quality", weight=0.15, evaluator_key="evidence_has_items_pass"
            ),
            MetricWeight(
                name="remediation_actionability", weight=0.20, evaluator_key="completeness_pass"
            ),
            MetricWeight(
                name="confidence_calibration", weight=0.10, evaluator_key="confidence_gte_pass"
            ),
            MetricWeight(name="no_hallucination", weight=0.10, evaluator_key="hallucination_pass"),
        ),
    ),
    "response_drafter": AgentMetricSpec(
        agent_name="response_drafter",
        metrics=(
            MetricWeight(name="relevance", weight=0.25, evaluator_key="relevance_pass"),
            MetricWeight(
                name="source_citation", weight=0.20, evaluator_key="sources_used_has_items_pass"
            ),
            MetricWeight(
                name="keyword_coverage", weight=0.15, evaluator_key="keyword_coverage_pass"
            ),
            MetricWeight(name="tone_quality", weight=0.15, evaluator_key="tone_pass"),
            MetricWeight(name="completeness", weight=0.15, evaluator_key="completeness_pass"),
            MetricWeight(
                name="no_generic_phrases", weight=0.10, evaluator_key="generic_phrase_pass"
            ),
        ),
    ),
    "chart_generator": AgentMetricSpec(
        agent_name="chart_generator",
        metrics=(
            MetricWeight(name="structure_valid", weight=0.35, evaluator_key="yaml_structure_pass"),
            MetricWeight(name="spec_coverage", weight=0.35, evaluator_key="spec_coverage_pass"),
            MetricWeight(name="file_count", weight=0.30, evaluator_key="file_count_gte_pass"),
        ),
    ),
    "intent_router": AgentMetricSpec(
        agent_name="intent_router",
        metrics=(
            MetricWeight(
                name="intent_accuracy", weight=0.60, evaluator_key="intent_exact_match_pass"
            ),
            MetricWeight(name="rationale_quality", weight=0.40, evaluator_key="coherence_pass"),
        ),
    ),
    "ticket_reviewer": AgentMetricSpec(
        agent_name="ticket_reviewer",
        metrics=(
            MetricWeight(
                name="category_accuracy", weight=0.30, evaluator_key="category_exact_match_pass"
            ),
            MetricWeight(
                name="urgency_accuracy", weight=0.25, evaluator_key="urgency_exact_match_pass"
            ),
            MetricWeight(name="question_relevance", weight=0.25, evaluator_key="relevance_pass"),
            MetricWeight(
                name="search_query_quality",
                weight=0.20,
                evaluator_key="search_queries_has_items_pass",
            ),
        ),
    ),
    "k8s_investigator": AgentMetricSpec(
        agent_name="k8s_investigator",
        metrics=(
            MetricWeight(name="faithfulness", weight=0.25, evaluator_key="faithfulness_pass"),
            MetricWeight(
                name="evidence_quality", weight=0.20, evaluator_key="evidence_has_items_pass"
            ),
            MetricWeight(
                name="remediation_actionability", weight=0.25, evaluator_key="completeness_pass"
            ),
            MetricWeight(
                name="confidence_calibration", weight=0.15, evaluator_key="confidence_gte_pass"
            ),
            MetricWeight(name="no_hallucination", weight=0.15, evaluator_key="hallucination_pass"),
        ),
    ),
}
