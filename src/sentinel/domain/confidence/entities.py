from __future__ import annotations

import enum

from pydantic import BaseModel


class ConfidenceLabel(enum.Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class IndividualScore(BaseModel):
    raw: float
    weighted: float


class ConfidenceComponents(BaseModel):
    source_count_score: IndividualScore
    relevance_score: IndividualScore
    recency_score: IndividualScore


class ConfidenceScore(BaseModel):
    label: ConfidenceLabel
    total: float
    components: ConfidenceComponents

    @staticmethod
    def from_total(total: float) -> ConfidenceScore:
        """
        Create a confidence score from a single float (legacy convenience method).

        Distributes the total evenly across all components.
        """
        label = _label_from_score(total)
        individual = IndividualScore(raw=total, weighted=total)
        return ConfidenceScore(
            label=label,
            total=total,
            components=ConfidenceComponents(
                source_count_score=individual,
                relevance_score=individual,
                recency_score=individual,
            ),
        )

    @staticmethod
    def from_factors(
        *,
        source_count: int,
        max_expected_sources: int = 5,
        relevance: float,
        recency: float,
        source_weight: float = 0.3,
        relevance_weight: float = 0.5,
        recency_weight: float = 0.2,
    ) -> ConfidenceScore:
        """
        Build a confidence score from independent factors.

        :param source_count: number of sources that contributed evidence
        :param max_expected_sources: threshold for a perfect source count score
        :param relevance: 0.0-1.0 relevance/quality of the evidence
        :param recency: 0.0-1.0 how recent the data is (1.0 = fresh)
        :param source_weight: weight for source count factor (default 0.3)
        :param relevance_weight: weight for relevance factor (default 0.5)
        :param recency_weight: weight for recency factor (default 0.2)
        """
        source_raw = min(source_count / max(max_expected_sources, 1), 1.0)
        relevance_raw = max(0.0, min(relevance, 1.0))
        recency_raw = max(0.0, min(recency, 1.0))

        source_weighted = source_raw * source_weight
        relevance_weighted = relevance_raw * relevance_weight
        recency_weighted = recency_raw * recency_weight

        total = source_weighted + relevance_weighted + recency_weighted
        label = _label_from_score(total)

        return ConfidenceScore(
            label=label,
            total=round(total, 4),
            components=ConfidenceComponents(
                source_count_score=IndividualScore(
                    raw=round(source_raw, 4),
                    weighted=round(source_weighted, 4),
                ),
                relevance_score=IndividualScore(
                    raw=round(relevance_raw, 4),
                    weighted=round(relevance_weighted, 4),
                ),
                recency_score=IndividualScore(
                    raw=round(recency_raw, 4),
                    weighted=round(recency_weighted, 4),
                ),
            ),
        )


def _label_from_score(score: float) -> ConfidenceLabel:
    if score >= 0.7:
        return ConfidenceLabel.HIGH
    if score >= 0.4:
        return ConfidenceLabel.MEDIUM
    return ConfidenceLabel.LOW
