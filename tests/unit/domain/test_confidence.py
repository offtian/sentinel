from __future__ import annotations

from sentinel.domain.confidence import entities


class TestConfidenceScore:
    def test_high_confidence(self):
        score = entities.ConfidenceScore.from_total(0.85)
        assert score.label == entities.ConfidenceLabel.HIGH
        assert score.total == 0.85

    def test_medium_confidence(self):
        score = entities.ConfidenceScore.from_total(0.55)
        assert score.label == entities.ConfidenceLabel.MEDIUM

    def test_low_confidence(self):
        score = entities.ConfidenceScore.from_total(0.2)
        assert score.label == entities.ConfidenceLabel.LOW

    def test_boundary_high(self):
        score = entities.ConfidenceScore.from_total(0.7)
        assert score.label == entities.ConfidenceLabel.HIGH

    def test_boundary_medium(self):
        score = entities.ConfidenceScore.from_total(0.4)
        assert score.label == entities.ConfidenceLabel.MEDIUM

    def test_boundary_low(self):
        score = entities.ConfidenceScore.from_total(0.39)
        assert score.label == entities.ConfidenceLabel.LOW
