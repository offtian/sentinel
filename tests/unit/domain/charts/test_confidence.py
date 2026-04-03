from __future__ import annotations

import pytest

from sentinel.domain.charts import confidence
from sentinel.domain.confidence import entities as confidence_entities


class TestCalculateChartConfidence:
    def test_perfect_score(self):
        # Given all factors at maximum
        score = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=1.0,
            retry_count=0,
        )

        # Then total is 1.0 and label is HIGH
        assert score.total == 1.0
        assert score.label == confidence_entities.ConfidenceLabel.HIGH

    def test_zero_score_when_everything_fails(self):
        # Given all factors at minimum
        score = confidence.calculate_chart_confidence(
            schema_valid=False,
            template_renders=False,
            template_has_warnings=False,
            policy_compliant=False,
            policy_auto_resolved=False,
            spec_coverage=0.0,
            retry_count=3,
        )

        # Then total is near 0 and label is LOW
        assert score.total == pytest.approx(0.01, abs=0.01)
        assert score.label == confidence_entities.ConfidenceLabel.LOW

    def test_medium_score_with_warnings_and_retries(self):
        # Given partial success
        score = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=True,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=0.8,
            retry_count=1,
        )

        # Then score is in MEDIUM or HIGH range
        assert 0.4 <= score.total <= 0.9

    def test_policy_auto_resolved_reduces_score(self):
        # Given policy was auto-resolved vs fully compliant
        auto_resolved = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=False,
            policy_auto_resolved=True,
            spec_coverage=1.0,
            retry_count=0,
        )

        fully_compliant = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=1.0,
            retry_count=0,
        )

        # Then auto-resolved score is lower than fully compliant
        assert auto_resolved.total < fully_compliant.total

    def test_retry_count_reduces_score(self):
        # Given different retry counts
        zero_retries = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=1.0,
            retry_count=0,
        )

        two_retries = confidence.calculate_chart_confidence(
            schema_valid=True,
            template_renders=True,
            template_has_warnings=False,
            policy_compliant=True,
            policy_auto_resolved=False,
            spec_coverage=1.0,
            retry_count=2,
        )

        # Then more retries means lower score
        assert two_retries.total < zero_retries.total
