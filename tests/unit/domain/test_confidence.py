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
