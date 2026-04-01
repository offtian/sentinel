from __future__ import annotations

from sentinel.domain.search import searcher
from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import holmes_adapter
from sentinel.domain.supervisor import entities as supervisor_entities
from sentinel.domain.supervisor import quality_gate
from sentinel.domain.support import entities as support_entities
from sentinel.domain.vendor_adapters.pagerduty import PagerDutyClient
from sentinel.interfaces.graphs import common, sre_investigation, support_review
from sentinel.utils import logs


async def supervise_sre_investigation(
    *,
    alert: sre_entities.Alert,
    holmes: holmes_adapter.BaseHolmesAdapter,
    max_retries: int = 1,
    classifier_model: str = "",
    analyser_model: str = "",
    pagerduty_client: PagerDutyClient | None = None,
    post_to_slack: bool = True,
    persist_fn: common.PersistInvestigationFn | None = None,
    trace_collector: common.TraceCollector | None = None,
    require_approval_below: float = 0.0,
    request_approval_fn: common.RequestApprovalFn | None = None,
    status_update_client: common.StatusUpdateClient | None = None,
) -> supervisor_entities.SupervisedResult[common.InvestigationReply]:
    """
    Run the SRE investigation pipeline with supervisor quality gating.

    Execute ``investigate_alert``, evaluate quality, and retry with adjusted
    parameters if the output does not pass the quality gate. Return a
    ``SupervisedResult`` wrapping the best reply with its verdict and decision.

    :param alert: the alert to investigate
    :param holmes: HolmesGPT adapter for observability data
    :param max_retries: maximum number of retries on quality failure (default 1)
    :param classifier_model: LLM model override for alert classification
    :param analyser_model: LLM model override for root cause analysis
    :param pagerduty_client: optional PagerDuty client for incident updates
    :param post_to_slack: whether to post results to Slack
    :param persist_fn: optional persistence callback
    :param trace_collector: optional trace collector for agent messages
    :param require_approval_below: confidence threshold for human approval
    :param request_approval_fn: callback to request human approval
    :param status_update_client: optional status update client for UI feedback
    """
    retry_count = 0
    best_reply: common.InvestigationReply | None = None
    best_verdict: supervisor_entities.QualityVerdict | None = None

    while retry_count <= max_retries:
        reply = await sre_investigation.investigate_alert(
            alert,
            holmes=holmes,
            classifier_model=classifier_model,
            analyser_model=analyser_model,
            pagerduty_client=pagerduty_client if retry_count == 0 else None,
            post_to_slack=post_to_slack if retry_count == 0 else False,
            persist_fn=persist_fn if retry_count == 0 else None,
            trace_collector=trace_collector,
            require_approval_below=require_approval_below,
            request_approval_fn=request_approval_fn,
            status_update_client=status_update_client,
        )

        verdict = quality_gate.evaluate_sre_quality(reply=reply)

        logs.log_event(
            "supervisor.sre_quality_check",
            params={
                "alert_id": alert.id,
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
                    "alert_id": alert.id,
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
            "alert_id": alert.id,
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
    ticket: support_entities.Ticket,
    max_retries: int = 1,
    document_searcher: searcher.BaseDocumentSearcher | None = None,
    ticket_searcher: searcher.BasePastTicketSearcher | None = None,
    reviewer_model: str = "",
    drafter_model: str = "",
    persist_fn: common.PersistTicketReviewFn | None = None,
    trace_collector: common.TraceCollector | None = None,
    status_update_client: common.StatusUpdateClient | None = None,
) -> supervisor_entities.SupervisedResult[common.SupportReply]:
    """
    Run the support review pipeline with supervisor quality gating.

    Execute ``review_ticket``, evaluate quality, and retry with adjusted
    parameters if the output does not pass the quality gate. Return a
    ``SupervisedResult`` wrapping the best reply with its verdict and decision.

    :param ticket: the support ticket to review
    :param max_retries: maximum number of retries on quality failure (default 1)
    :param document_searcher: optional document search adapter
    :param ticket_searcher: optional past-ticket search adapter
    :param reviewer_model: LLM model override for ticket classification
    :param drafter_model: LLM model override for response drafting
    :param persist_fn: optional persistence callback
    :param trace_collector: optional trace collector for agent messages
    :param status_update_client: optional status update client for UI feedback
    """
    retry_count = 0
    best_reply: common.SupportReply | None = None
    best_verdict: supervisor_entities.QualityVerdict | None = None

    while retry_count <= max_retries:
        reply = await support_review.review_ticket(
            ticket,
            document_searcher=document_searcher,
            ticket_searcher=ticket_searcher,
            reviewer_model=reviewer_model,
            drafter_model=drafter_model,
            persist_fn=persist_fn if retry_count == 0 else None,
            trace_collector=trace_collector,
            status_update_client=status_update_client,
        )

        verdict = quality_gate.evaluate_support_quality(reply=reply)

        logs.log_event(
            "supervisor.support_quality_check",
            params={
                "ticket_key": ticket.key,
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
                    "ticket_key": ticket.key,
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
            "ticket_key": ticket.key,
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
