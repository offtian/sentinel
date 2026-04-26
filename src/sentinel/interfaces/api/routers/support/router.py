from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import fastapi
from pydantic import BaseModel

from sentinel.config import get_config
from sentinel.data import db as async_db
from sentinel.data.primitives import envelope as envelope_mod
from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.jobs import operations as job_ops
from sentinel.domain.support import entities
from sentinel.domain.support import operations as support_ops
from sentinel.domain.support import queries as support_queries
from sentinel.domain.support.entities import ReviewStatus
from sentinel.interfaces.api.dependencies import require_database
from sentinel.interfaces.webhooks import envelope_factory
from sentinel.interfaces.workflows import support_review as workflows_support_review
from sentinel.settings import settings
from sentinel.utils import logs


router = fastapi.APIRouter(prefix="/support", tags=["support"])


def _envelope_payload(envelope: envelope_mod.Envelope) -> dict[str, Any]:
    """
    Return the envelope identity fields as a sub-dict for the queued payload.

    Mirror of the SRE router helper. The worker's ``_envelope_for_job``
    rehydrates the same keys so the support pipeline run inherits the
    ingress-time tenant context.
    """
    return {
        "tenant_id": envelope.tenant_id,
        "cluster_id": envelope.cluster_id,
        "region": envelope.region,
        "pii_class": envelope.pii_class,
        "ingress_request_id": str(envelope.request_id),
        "received_at": envelope.received_at.isoformat(),
    }


async def _enqueue_ticket(
    ticket: entities.Ticket,
    *,
    requested_by: str,
    priority: int = 2,
    envelope: envelope_mod.Envelope | None = None,
) -> fastapi.responses.JSONResponse:
    """Enqueue a ticket for async review and return 202 Accepted."""
    db = async_db.get_db()
    ticket_payload: dict[str, Any] = ticket.model_dump(mode="json")
    if envelope is not None:
        ticket_payload = {**ticket_payload, **_envelope_payload(envelope)}
    job_id = await job_ops.enqueue_review(
        db=db,
        ticket_payload=ticket_payload,
        requested_by=requested_by,
        ticket_id=ticket.id,
        priority=priority,
    )

    return fastapi.responses.JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": str(job_id),
            "ticket_key": ticket.key,
        },
    )


def _envelope_ingress_failure_response(
    error: envelope_factory.EnvelopeIngressError,
) -> fastapi.responses.JSONResponse:
    """Return a 422 response describing why envelope ingress failed."""
    return fastapi.responses.JSONResponse(
        status_code=422,
        content={
            "error": "envelope_ingress_missing_tenant_id",
            "source": error.source,
            "request_id": str(error.request_id),
        },
    )


@router.post("/webhooks/jira")
async def handle_jira_webhook(
    request: fastapi.Request,
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """
    Receive Jira Service Desk webhook events and run the support-review
    workflow synchronously against the compiled LangGraph instance.

    Jira webhooks fire on issue creation/update. After the T17 hard-cut,
    actionable events drive ``workflows.support_review.review_ticket``
    against ``request.app.state.support_review_graph`` and surface the
    structured outcome inline (rather than enqueuing a job for the
    worker). The audit row in the support-review table is persisted
    after the graph returns so reporting still has a complete trail
    whether the run completed or paused at the approval gate.
    """
    webhook_event = payload.get("webhookEvent", "")
    issue = payload.get("issue", {})

    if webhook_event not in ("jira:issue_created", "jira:issue_updated"):
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "skipped", "reason": f"Event type {webhook_event} not handled"},
        )

    fields = issue.get("fields", {})
    ticket = entities.Ticket(
        id=str(issue.get("id", "")),
        key=issue.get("key", ""),
        summary=fields.get("summary", ""),
        description=fields.get("description", "") or "",
        reporter=fields.get("reporter", {}).get("displayName", "Unknown"),
        priority=fields.get("priority", {}).get("name", "Medium"),
        created_at=datetime.now(tz=UTC),
        labels=fields.get("labels", []),
        raw_payload=payload,
    )

    logs.log_event(
        "jira_ticket_received",
        params={"ticket_key": ticket.key, "summary": ticket.summary},
    )

    try:
        envelope = envelope_factory.envelope_from_jira(
            payload=payload,
            request_id=request.state.request_id,
            settings=settings,
            strict=get_config().envelope_strict_mode,
        )
    except envelope_factory.EnvelopeIngressError as exc:
        return _envelope_ingress_failure_response(exc)

    if not settings.support_auto_draft:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "received", "ticket_key": ticket.key, "auto_draft": False},
        )

    graph = getattr(request.app.state, "support_review_graph", None)
    if graph is None:
        logs.log_event(
            "support_review_graph_unavailable",
            params={"ticket_key": ticket.key, "request_id": str(envelope.request_id)},
        )
        return fastapi.responses.JSONResponse(
            status_code=503,
            content={
                "error": "support_review_graph_unavailable",
                "request_id": str(envelope.request_id),
            },
        )

    outcome = await workflows_support_review.review_ticket(
        ticket=ticket,
        envelope=envelope,
        graph=graph,
    )

    await _persist_review_outcome(ticket=ticket, outcome=outcome)

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "request_id": str(outcome.request_id),
            "ticket_key": ticket.key,
            "needs_approval": outcome.needs_approval,
            "interrupt_payload": outcome.interrupt_payload,
            "suggestion_id": (
                str(outcome.response_suggestion.id)
                if outcome.response_suggestion is not None
                else None
            ),
        },
    )


async def _persist_review_outcome(
    *,
    ticket: entities.Ticket,
    outcome: workflows_support_review.ReviewOutcome,
) -> None:
    """
    Persist the audit row for a synchronous review run.

    No-ops when the graph paused before any suggestion was drafted (the
    interrupt-before-draft case is impossible today but the guard keeps
    the persistence call total). The DB connection is the same singleton
    the worker pipeline reads, so the audit row format is unchanged.
    """
    suggestion = outcome.response_suggestion
    if suggestion is None:
        return
    db = async_db.get_db()
    sources_json = (
        {"sources": [source.model_dump(mode="json") for source in suggestion.sources]}
        if suggestion.sources
        else None
    )
    confidence_score = outcome.confidence.total if outcome.confidence is not None else None
    await support_ops.persist_ticket_review(
        db=db,
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        suggested_response=suggestion.suggested_response,
        sources_json=sources_json,
        confidence_score=confidence_score,
        category=suggestion.category,
    )


@router.post("/review")
async def trigger_review(
    request: fastapi.Request,
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """
    Manually trigger a ticket review.

    Expects:
    {
        "id": "12345",
        "key": "SUPPORT-123",
        "summary": "Cannot log in to dashboard",
        "description": "I've been unable to log in since...",
        "reporter": "John Doe",
        "priority": "High"
    }

    The envelope minted here uses the ``"manual"`` tenant sentinel so ops
    queries can distinguish API-driven runs from real ingress.
    """
    ticket = entities.Ticket(
        id=payload.get("id", "manual"),
        key=payload.get("key", "MANUAL-1"),
        summary=payload.get("summary", ""),
        description=payload.get("description", ""),
        reporter=payload.get("reporter", "Unknown"),
        priority=payload.get("priority", "Medium"),
        created_at=datetime.now(tz=UTC),
        labels=payload.get("labels", []),
        raw_payload=payload,
    )

    envelope = envelope_factory.envelope_for_manual(
        request_id=request.state.request_id,
        settings=settings,
    )
    return await _enqueue_ticket(ticket, requested_by="api:manual", envelope=envelope)


@router.get("/reviews/{review_id}", dependencies=[fastapi.Depends(require_database)])
async def get_review(
    review_id: uuid.UUID,
) -> fastapi.responses.JSONResponse:
    """
    Return a ticket review record by its ID.

    Returns 404 if the review is not found.
    """
    db = async_db.get_db()
    record = await support_queries.fetch_ticket_review(db=db, record_id=review_id)

    if record is None:
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={"error": "Review not found", "review_id": str(review_id)},
        )

    reviewed_at = record["reviewed_at"]
    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "review_id": str(record["id"]),
            "ticket_id": record["ticket_id"],
            "ticket_key": record["ticket_key"],
            "suggested_response": record["suggested_response"],
            "sources": record["sources_json"],
            "confidence_score": record["confidence_score"],
            "category": record["category"],
            "status": record["status"],
            "created_at": record["created_at"].isoformat(),
            "reviewed_at": reviewed_at.isoformat() if reviewed_at else None,
        },
    )


@router.get("/stats", dependencies=[fastapi.Depends(require_database)])
async def get_support_stats() -> fastapi.responses.JSONResponse:
    """
    Return acceptance/rejection rates for support review suggestions.

    Provides counts of reviews grouped by status (drafted, accepted, rejected, modified).
    """
    db = async_db.get_db()
    counts = await support_queries.fetch_review_stats(db=db)

    total = sum(counts.values())
    reviewed = total - counts.get("drafted", 0)

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "total_reviews": total,
            "total_reviewed": reviewed,
            "counts": counts,
            "acceptance_rate": (
                round(counts.get("accepted", 0) / reviewed, 3) if reviewed > 0 else None
            ),
        },
    )


@router.post(
    "/reviews/{review_id}/feedback",
    dependencies=[fastapi.Depends(require_database)],
)
async def submit_review_feedback(
    review_id: uuid.UUID,
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """
    Update the status of a ticket review (accepted, rejected, modified).

    Expects: {"status": "accepted" | "rejected" | "modified"}
    """
    new_status = payload.get("status", "")
    feedback_values = {s.value for s in ReviewStatus if s != ReviewStatus.DRAFTED}
    if new_status not in feedback_values:
        return fastapi.responses.JSONResponse(
            status_code=400,
            content={
                "error": f"Invalid status. Must be one of: {', '.join(sorted(feedback_values))}",
                "received": new_status,
            },
        )

    db = async_db.get_db()
    record = await support_queries.fetch_ticket_review(db=db, record_id=review_id)
    if record is None:
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={"error": "Review not found", "review_id": str(review_id)},
        )

    await support_ops.update_review_status(db=db, record_id=review_id, status=new_status)

    logs.log_event(
        "review_feedback_submitted",
        params={"review_id": str(review_id), "status": new_status},
    )

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "review_id": str(review_id),
            "status": new_status,
            "reviewed_at": datetime.now(tz=UTC).isoformat(),
        },
    )


# ── Approval Gate ────────────────────────────────────────────────────────────


class _ApproveBody(BaseModel):
    approver: str
    edits: str | None = None


class _RejectBody(BaseModel):
    approver: str
    reason: str | None = None


def _graph_unavailable_response(request_id: uuid.UUID) -> fastapi.responses.JSONResponse:
    """Return 503 when the lifespan never wired the support-review graph."""
    return fastapi.responses.JSONResponse(
        status_code=503,
        content={
            "error": "support_review_graph_unavailable",
            "request_id": str(request_id),
        },
    )


def _thread_not_found_response(request_id: uuid.UUID) -> fastapi.responses.JSONResponse:
    """Return 404 when no checkpoint exists for the given request_id."""
    return fastapi.responses.JSONResponse(
        status_code=404,
        content={
            "error": "support_review_thread_not_found",
            "request_id": str(request_id),
        },
    )


def _already_decided_response(
    *,
    request_id: uuid.UUID,
    status: str,
) -> fastapi.responses.JSONResponse:
    """Return 409 when the thread is no longer pending approval."""
    return fastapi.responses.JSONResponse(
        status_code=409,
        content={
            "error": "support_review_already_decided",
            "request_id": str(request_id),
            "status": status,
        },
    )


@router.post("/responses/{request_id}/approve")
async def approve_response_suggestion(
    request: fastapi.Request,
    request_id: uuid.UUID,
    action: _ApproveBody,
) -> fastapi.responses.JSONResponse:
    """
    Approve a paused support response suggestion and resume the workflow.

    The endpoint reads the compiled graph off
    ``request.app.state.support_review_graph`` and resumes the thread
    via ``workflows.support_review.resume_review``. The pre-resume
    status check guards against duplicate approvals -- once a thread has
    transitioned out of ``pending`` the endpoint returns 409 rather than
    silently re-running the graph.
    """
    graph = getattr(request.app.state, "support_review_graph", None)
    if graph is None:
        return _graph_unavailable_response(request_id)

    review_status = await workflows_support_review.get_review_status(
        request_id=request_id,
        graph=graph,
    )
    if review_status is None:
        return _thread_not_found_response(request_id)
    if review_status.status != "pending":
        return _already_decided_response(
            request_id=request_id,
            status=review_status.status,
        )

    outcome = await workflows_support_review.resume_review(
        request_id=request_id,
        decision=approval_entities.ApprovalDecision.APPROVED,
        graph=graph,
        approver=action.approver,
        reason=action.edits,
    )

    logs.log_event(
        "support_review_approved",
        params={"request_id": str(request_id), "approver": action.approver},
    )

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "request_id": str(outcome.request_id),
            "status": "approved",
            "approver": action.approver,
            "approved_at": datetime.now(tz=UTC).isoformat(),
        },
    )


@router.post("/responses/{request_id}/reject")
async def reject_response_suggestion(
    request: fastapi.Request,
    request_id: uuid.UUID,
    action: _RejectBody,
) -> fastapi.responses.JSONResponse:
    """
    Reject a paused support response suggestion and resume the workflow.

    Mirror of :func:`approve_response_suggestion`: the same status check
    guards against duplicate decisions, and the resume payload threads
    the optional ``reason`` string through to the audit trail.
    """
    graph = getattr(request.app.state, "support_review_graph", None)
    if graph is None:
        return _graph_unavailable_response(request_id)

    review_status = await workflows_support_review.get_review_status(
        request_id=request_id,
        graph=graph,
    )
    if review_status is None:
        return _thread_not_found_response(request_id)
    if review_status.status != "pending":
        return _already_decided_response(
            request_id=request_id,
            status=review_status.status,
        )

    outcome = await workflows_support_review.resume_review(
        request_id=request_id,
        decision=approval_entities.ApprovalDecision.REJECTED,
        graph=graph,
        approver=action.approver,
        reason=action.reason,
    )

    logs.log_event(
        "support_review_rejected",
        params={"request_id": str(request_id), "approver": action.approver},
    )

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "request_id": str(outcome.request_id),
            "status": "rejected",
            "approver": action.approver,
            "rejected_at": datetime.now(tz=UTC).isoformat(),
        },
    )


@router.get("/responses/{request_id}/approval-status")
async def get_response_approval_status(
    request: fastapi.Request,
    request_id: uuid.UUID,
) -> fastapi.responses.JSONResponse:
    """
    Return the current approval status of a support-review thread.

    Reads the thread snapshot via
    ``workflows.support_review.get_review_status``; surfaces 404 when no
    checkpoint exists for the request_id and 503 when the lifespan never
    wired the graph.
    """
    graph = getattr(request.app.state, "support_review_graph", None)
    if graph is None:
        return _graph_unavailable_response(request_id)

    review_status = await workflows_support_review.get_review_status(
        request_id=request_id,
        graph=graph,
    )
    if review_status is None:
        return _thread_not_found_response(request_id)

    decision_value: str | None = None
    if review_status.approval_decision is not None:
        decision_value = review_status.approval_decision.value

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "request_id": str(request_id),
            "status": review_status.status,
            "needs_approval": review_status.needs_approval,
            "approval_decision": decision_value,
        },
    )
