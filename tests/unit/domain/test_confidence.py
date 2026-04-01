from __future__ import annotations

import pytest

from sentinel.domain.confidence import entities


class TestConfidenceScoreFromTotal:
    def test_high_confidence(self):
        # Given a score above the HIGH threshold
        # When constructing from total
        score = entities.ConfidenceScore.from_total(0.75)

        # Then the label is HIGH
        assert score.label == entities.ConfidenceLabel.HIGH
        assert score.total == 0.75

    def test_medium_confidence(self):
        # Given a score in the MEDIUM range
        # When constructing from total
        score = entities.ConfidenceScore.from_total(0.55)

        # Then the label is MEDIUM
        assert score.label == entities.ConfidenceLabel.MEDIUM

    def test_low_confidence(self):
        # Given a score below the LOW threshold
        # When constructing from total
        score = entities.ConfidenceScore.from_total(0.2)

        # Then the label is LOW
        assert score.label == entities.ConfidenceLabel.LOW

    def test_boundary_high_threshold(self):
        # Given a score exactly at the HIGH boundary (0.7)
        # When constructing from total
        score = entities.ConfidenceScore.from_total(0.7)

        # Then it is HIGH (inclusive)
        assert score.label == entities.ConfidenceLabel.HIGH

    def test_boundary_medium_threshold(self):
        # Given a score exactly at the MEDIUM boundary (0.4)
        # When constructing from total
        score = entities.ConfidenceScore.from_total(0.4)

        # Then it is MEDIUM (inclusive)
        assert score.label == entities.ConfidenceLabel.MEDIUM

    def test_components_populated(self):
        # Given a high confidence score
        # When constructing from total
        score = entities.ConfidenceScore.from_total(0.8)

        # Then all component fields are populated
        assert score.components.source_count_score.raw == 0.8
        assert score.components.relevance_score.weighted == 0.8
        assert score.components.recency_score.raw == 0.8


class TestConfidenceLabel:
    def test_label_values(self):
        # Given the ConfidenceLabel enum
        # When checking values
        # Then they match expected strings
        assert entities.ConfidenceLabel.HIGH.value == "High"
        assert entities.ConfidenceLabel.MEDIUM.value == "Medium"
        assert entities.ConfidenceLabel.LOW.value == "Low"


class TestIndividualScore:
    def test_create_individual_score(self):
        # Given raw and weighted floats
        # When creating an IndividualScore
        score = entities.IndividualScore(raw=0.9, weighted=0.72)

        # Then values are stored correctly
        assert score.raw == 0.9
        assert score.weighted == 0.72

    @pytest.mark.parametrize("value", [-0.1, 1.1, 5.0])
    def test_scores_allow_any_float(self, value: float):
        # Given a float value (pydantic does not constrain range here)
        # When creating an IndividualScore
        # Then no error is raised
        score = entities.IndividualScore(raw=value, weighted=value)
        assert score.raw == value


class TestConfidenceScoreFromFactors:
    def test_high_confidence_with_many_sources(self):
        # Given many relevant, recent sources
        # When building from factors
        score = entities.ConfidenceScore.from_factors(
            source_count=5,
            max_expected_sources=5,
            relevance=0.9,
            recency=0.9,
        )

        # Then confidence is HIGH
        assert score.label == entities.ConfidenceLabel.HIGH
        # 0.3*1.0 + 0.5*0.9 + 0.2*0.9 = 0.3 + 0.45 + 0.18 = 0.93
        assert score.total == pytest.approx(0.93, abs=0.01)

    def test_low_confidence_with_no_sources(self):
        # Given zero sources and low relevance
        # When building from factors
        score = entities.ConfidenceScore.from_factors(
            source_count=0,
            relevance=0.2,
            recency=0.5,
        )

        # Then confidence is LOW
        assert score.label == entities.ConfidenceLabel.LOW
        # 0.3*0.0 + 0.5*0.2 + 0.2*0.5 = 0 + 0.1 + 0.1 = 0.2
        assert score.total == pytest.approx(0.2, abs=0.01)

    def test_medium_confidence_with_partial_data(self):
        # Given some sources with moderate relevance
        # When building from factors
        score = entities.ConfidenceScore.from_factors(
            source_count=2,
            max_expected_sources=5,
            relevance=0.7,
            recency=0.6,
        )

        # Then confidence is MEDIUM
        assert score.label == entities.ConfidenceLabel.MEDIUM
        # 0.3*0.4 + 0.5*0.7 + 0.2*0.6 = 0.12 + 0.35 + 0.12 = 0.59
        assert score.total == pytest.approx(0.59, abs=0.01)

    def test_components_are_individually_weighted(self):
        # Given specific factor values
        # When building from factors
        score = entities.ConfidenceScore.from_factors(
            source_count=3,
            max_expected_sources=5,
            relevance=0.8,
            recency=0.5,
        )

        # Then each component has correct raw and weighted values
        assert score.components.source_count_score.raw == pytest.approx(0.6, abs=0.01)
        assert score.components.source_count_score.weighted == pytest.approx(0.18, abs=0.01)
        assert score.components.relevance_score.raw == pytest.approx(0.8, abs=0.01)
        assert score.components.relevance_score.weighted == pytest.approx(0.4, abs=0.01)
        assert score.components.recency_score.raw == pytest.approx(0.5, abs=0.01)
        assert score.components.recency_score.weighted == pytest.approx(0.1, abs=0.01)

    def test_source_count_capped_at_max(self):
        # Given more sources than max_expected
        # When building from factors
        score = entities.ConfidenceScore.from_factors(
            source_count=10,
            max_expected_sources=5,
            relevance=0.5,
            recency=0.5,
        )

        # Then source_count_score.raw is capped at 1.0
        assert score.components.source_count_score.raw == pytest.approx(1.0)

    def test_relevance_clamped_to_valid_range(self):
        # Given out-of-range relevance
        # When building from factors
        score = entities.ConfidenceScore.from_factors(
            source_count=1,
            relevance=1.5,
            recency=-0.3,
        )

        # Then values are clamped
        assert score.components.relevance_score.raw == pytest.approx(1.0)
        assert score.components.recency_score.raw == pytest.approx(0.0)
