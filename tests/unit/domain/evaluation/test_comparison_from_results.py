"""
Tests for ComparisonResult.from_investigation_results() factory method.
"""

from __future__ import annotations

import pytest

from sentinel.domain.evaluation import comparison
from tests import factories


class TestFromInvestigationResults:
    def test_computes_evidence_source_count(self) -> None:
        # Given a baseline with 3 sources and a challenger with 1 source
        baseline_result = factories.make_investigation_result(
            sources_queried=("kubernetes", "datadog_logs", "pagerduty"),
            adapter_name="native_k8s",
        )
        challenger_result = factories.make_investigation_result(
            sources_queried=("kagent_crd",),
            adapter_name="kagent",
        )

        # When a ComparisonResult is built from the results
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=baseline_result,
            challenger=challenger_result,
        )

        # Then the evidence source counts reflect the number of sources queried
        assert result.baseline.evidence_source_count == 3
        assert result.challenger.evidence_source_count == 1

    def test_computes_latency(self) -> None:
        # Given a baseline with 500ms duration and a challenger with 200ms duration
        slow_baseline = factories.make_investigation_result(
            duration_ms=500,
            adapter_name="native_k8s",
        )
        fast_challenger = factories.make_investigation_result(
            duration_ms=200,
            adapter_name="kagent",
        )

        # When a ComparisonResult is built from the results
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=slow_baseline,
            challenger=fast_challenger,
        )

        # Then latency_p50_ms matches the duration_ms of each result
        assert result.baseline.latency_p50_ms == 500
        assert result.challenger.latency_p50_ms == 200

    def test_computes_degradation_score_from_audit_trail(self) -> None:
        # Given a challenger with 1 error in 2 audit trail entries
        successful_entry = factories.make_audit_entry(
            adapter_name="kagent",
            status="success",
            error_code=None,
        )
        failed_entry = factories.make_audit_entry(
            adapter_name="kagent",
            status="error",
            error_code="TOOL_TIMEOUT",
        )
        challenger_result = factories.make_investigation_result(
            adapter_name="kagent",
            audit_trail=(successful_entry, failed_entry),
        )
        baseline_result = factories.make_investigation_result(
            adapter_name="native_k8s",
        )

        # When a ComparisonResult is built from the results
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=baseline_result,
            challenger=challenger_result,
        )

        # Then the challenger degradation score is 0.5 (1 error out of 2 entries)
        assert result.challenger.degradation_score == pytest.approx(0.5)

    def test_winner_by_dimension_picks_better_value(self) -> None:
        # Given a baseline with more sources but slower latency, challenger with fewer sources but faster
        source_rich_baseline = factories.make_investigation_result(
            sources_queried=("kubernetes", "datadog_logs", "pagerduty"),
            duration_ms=800,
            adapter_name="native_k8s",
        )
        fast_challenger = factories.make_investigation_result(
            sources_queried=("kagent_crd",),
            duration_ms=200,
            adapter_name="kagent",
        )

        # When a ComparisonResult is built from the results
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=source_rich_baseline,
            challenger=fast_challenger,
        )

        # Then evidence_source_count winner is the baseline (more sources = better)
        # and latency winner is the challenger (lower latency = better)
        assert result.winner_by_dimension["evidence_source_count"] == "native_k8s"
        assert result.winner_by_dimension["latency_p50_ms"] == "kagent"

    def test_handles_empty_audit_trail(self) -> None:
        # Given both baseline and challenger with empty audit trails
        baseline_result = factories.make_investigation_result(
            adapter_name="native_k8s",
            audit_trail=(),
        )
        challenger_result = factories.make_investigation_result(
            adapter_name="kagent",
            audit_trail=(),
        )

        # When a ComparisonResult is built from the results
        result = comparison.ComparisonResult.from_investigation_results(
            baseline=baseline_result,
            challenger=challenger_result,
        )

        # Then degradation_score defaults to 1.0 for both (no errors observed)
        assert result.baseline.degradation_score == pytest.approx(1.0)
        assert result.challenger.degradation_score == pytest.approx(1.0)
