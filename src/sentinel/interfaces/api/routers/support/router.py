from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import fastapi

from sentinel.config import get_config
from sentinel.data import db as async_db
from sentinel.data import envelope as envelope_mod
from sentinel.domain.jobs import operations as job_ops
from sentinel.domain.support import entities
from sentinel.domain.support import operations as support_ops
from sentinel.domain.support import queries as support_queries
from sentinel.domain.support.entities import ReviewStatus
from sentinel.interfaces.api.dependencies import require_database
from sentinel.interfaces.webhooks import envelope_factory
from sentinel.settings import get_settings
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
    Receive Jira Service Desk webhook events and enqueue for async review.

    Jira webhooks fire on issue creation/update. We enqueue new tickets
    for background response suggestion generation.
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
            settings=get_settings(),
            strict=get_config().envelope_strict_mode,
        )
    except envelope_factory.EnvelopeIngressError as exc:
        return _envelope_ingress_failure_response(exc)

    if not get_settings().support_auto_draft:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "received", "ticket_key": ticket.key, "auto_draft": False},
        )

    return await _enqueue_ticket(ticket, requested_by="webhook:jira", envelope=envelope)


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
        settings=get_settings(),
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
