from __future__ import annotations

import enum

import attrs

from sentinel.domain.confidence import entities as confidence_entities


class SupervisorDecision(enum.Enum):
    """
    Decision made by the supervisor after quality evaluation.

    PUBLISH: quality is sufficient, output can be delivered.
    RETRY: quality is insufficient but retries remain.
    ESCALATE: quality is insufficient and retries exhausted -- needs human review.
    REJECT: output is fundamentally flawed (e.g. hallucination detected).
    """

    PUBLISH = "publish"
    RETRY = "retry"
    ESCALATE = "escalate"
    REJECT = "reject"


@attrs.frozen
class QualityVerdict:
    """
    Result of a rule-based quality evaluation on a pipeline output.

    Immutable value object. ``issues`` captures every problem detected;
    ``passed`` is True only when no issues were found.
    """

    passed: bool
    issues: tuple[str, ...]
    score: float  # 0.0 (worst) to 1.0 (best)


@attrs.frozen
class SupervisedResult[ReplyT]:
    """
    Wrap a pipeline reply with supervisor metadata.

    Generic over the reply type so the same structure works for both
    ``InvestigationReply`` and ``SupportReply``.
    """

    reply: ReplyT
    verdict: QualityVerdict
    decision: SupervisorDecision
    retry_count: int
    confidence: confidence_entities.ConfidenceScore | None = None
