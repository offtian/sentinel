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
        if total >= 0.7:
            label = ConfidenceLabel.HIGH
        elif total >= 0.4:
            label = ConfidenceLabel.MEDIUM
        else:
            label = ConfidenceLabel.LOW

        return ConfidenceScore(
            label=label,
            total=total,
            components=ConfidenceComponents(
                source_count_score=IndividualScore(raw=total, weighted=total),
                relevance_score=IndividualScore(raw=total, weighted=total),
                recency_score=IndividualScore(raw=total, weighted=total),
            ),
        )
