from __future__ import annotations

from collections.abc import Awaitable, Callable

from sentinel.domain.pipeline import types as pipeline_types
from sentinel.domain.supervisor import entities as supervisor_entities
from sentinel.domain.supervisor import quality_gate
from sentinel.utils import logs


# Callable signatures for the pipeline entry-point functions injected by callers.
InvestigateFn = Callable[..., Awaitable[pipeline_types.InvestigationReply]]
ReviewFn = Callable[..., Awaitable[pipeline_types.SupportReply]]


async def supervise_sre_investigation(
    *,
    investigate_fn: InvestigateFn,
    alert_id: str,
    max_retries: int = 1,
    first_run_kwargs: dict[str, object] | None = None,
    retry_kwargs: dict[str, object] | None = None,
) -> supervisor_entities.SupervisedResult[pipeline_types.InvestigationReply]:
    """
    Run the SRE investigation pipeline with supervisor quality gating.

    Execute ``investigate_fn``, evaluate quality, and retry with adjusted
    parameters if the output does not pass the quality gate. Return a
    ``SupervisedResult`` wrapping the best reply with its verdict and decision.

    :param investigate_fn: callable that runs the investigation pipeline
    :param alert_id: alert identifier for logging
    :param max_retries: maximum number of retries on quality failure (default 1)
    :param first_run_kwargs: kwargs passed to investigate_fn on the first attempt
    :param retry_kwargs: kwargs passed to investigate_fn on retry attempts
    """
    first_kw = first_run_kwargs or {}
    retry_kw = retry_kwargs or {}

    retry_count = 0
    best_reply: pipeline_types.InvestigationReply | None = None
    best_verdict: supervisor_entities.QualityVerdict | None = None

    while retry_count <= max_retries:
        kwargs = first_kw if retry_count == 0 else retry_kw
        reply = await investigate_fn(**kwargs)

        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        logs.log_event(
            "supervisor.sre_quality_check",
            params={
                "alert_id": alert_id,
                "retry_count": retry_count,
                "passed": verdict.passed,
                "score": verdict.score,
                "issues": verdict.issues,
            },
        )

        # Track the best result across retries.
        if best_verdict is None or verdict.score > best_verdict.score:
            best_reply = reply
            best_verdict = verdict

        if verdict.passed:
            logs.log_event(
                "supervisor.sre_decision",
                params={
                    "alert_id": alert_id,
                    "decision": supervisor_entities.SupervisorDecision.PUBLISH.value,
                    "retry_count": retry_count,
                },
            )
            return supervisor_entities.SupervisedResult(
                reply=reply,
                verdict=verdict,
                decision=supervisor_entities.SupervisorDecision.PUBLISH,
                retry_count=retry_count,
                confidence=reply.confidence,
            )

        retry_count += 1

    # All retries exhausted -- decide between ESCALATE and REJECT.
    assert best_reply is not None
    assert best_verdict is not None

    decision = _decide_on_failure(verdict=best_verdict)

    logs.log_event(
        "supervisor.sre_decision",
        params={
            "alert_id": alert_id,
            "decision": decision.value,
            "retry_count": retry_count - 1,
            "best_score": best_verdict.score,
            "issues": best_verdict.issues,
        },
    )

    return supervisor_entities.SupervisedResult(
        reply=best_reply,
        verdict=best_verdict,
        decision=decision,
        retry_count=retry_count - 1,
        confidence=best_reply.confidence,
    )


async def supervise_support_review(
    *,
    review_fn: ReviewFn,
    ticket_key: str,
    max_retries: int = 1,
    first_run_kwargs: dict[str, object] | None = None,
    retry_kwargs: dict[str, object] | None = None,
) -> supervisor_entities.SupervisedResult[pipeline_types.SupportReply]:
    """
    Run the support review pipeline with supervisor quality gating.

    Execute ``review_fn``, evaluate quality, and retry with adjusted
    parameters if the output does not pass the quality gate. Return a
    ``SupervisedResult`` wrapping the best reply with its verdict and decision.

    :param review_fn: callable that runs the support review pipeline
    :param ticket_key: ticket identifier for logging
    :param max_retries: maximum number of retries on quality failure (default 1)
    :param first_run_kwargs: kwargs passed to review_fn on the first attempt
    :param retry_kwargs: kwargs passed to review_fn on retry attempts
    """
    first_kw = first_run_kwargs or {}
    retry_kw = retry_kwargs or {}

    retry_count = 0
    best_reply: pipeline_types.SupportReply | None = None
    best_verdict: supervisor_entities.QualityVerdict | None = None

    while retry_count <= max_retries:
        kwargs = first_kw if retry_count == 0 else retry_kw
        reply = await review_fn(**kwargs)

        verdict = quality_gate.evaluate_support_quality(reply=reply)

        logs.log_event(
            "supervisor.support_quality_check",
            params={
                "ticket_key": ticket_key,
                "retry_count": retry_count,
                "passed": verdict.passed,
                "score": verdict.score,
                "issues": verdict.issues,
            },
        )

        if best_verdict is None or verdict.score > best_verdict.score:
            best_reply = reply
            best_verdict = verdict

        if verdict.passed:
            logs.log_event(
                "supervisor.support_decision",
                params={
                    "ticket_key": ticket_key,
                    "decision": supervisor_entities.SupervisorDecision.PUBLISH.value,
                    "retry_count": retry_count,
                },
            )
            return supervisor_entities.SupervisedResult(
                reply=reply,
                verdict=verdict,
                decision=supervisor_entities.SupervisorDecision.PUBLISH,
                retry_count=retry_count,
                confidence=reply.confidence,
            )

        retry_count += 1

    assert best_reply is not None
    assert best_verdict is not None

    decision = _decide_on_failure(verdict=best_verdict)

    logs.log_event(
        "supervisor.support_decision",
        params={
            "ticket_key": ticket_key,
            "decision": decision.value,
            "retry_count": retry_count - 1,
            "best_score": best_verdict.score,
            "issues": best_verdict.issues,
        },
    )

    return supervisor_entities.SupervisedResult(
        reply=best_reply,
        verdict=best_verdict,
        decision=decision,
        retry_count=retry_count - 1,
        confidence=best_reply.confidence,
    )


def _decide_on_failure(
    *,
    verdict: supervisor_entities.QualityVerdict,
) -> supervisor_entities.SupervisorDecision:
    """
    Determine whether a failed quality check should escalate or reject.

    REJECT when the score is very low (< 0.3), indicating fundamentally
    flawed output. ESCALATE otherwise, so a human can review and salvage
    the partial results.
    """
    if verdict.score < 0.3:
        return supervisor_entities.SupervisorDecision.REJECT
    return supervisor_entities.SupervisorDecision.ESCALATE
