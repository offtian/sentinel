from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import fastapi

from sentinel.data import db as async_db
from sentinel.domain.jobs import operations as job_ops
from sentinel.domain.support import entities
from sentinel.domain.support import operations as support_ops
from sentinel.domain.support import queries as support_queries
from sentinel.domain.support.entities import ReviewStatus
from sentinel.interfaces.api.dependencies import require_database
from sentinel.settings import get_settings
from sentinel.utils import logs


router = fastapi.APIRouter(prefix="/support", tags=["support"])


async def _enqueue_ticket(
    ticket: entities.Ticket,
    *,
    requested_by: str,
    priority: int = 2,
) -> fastapi.responses.JSONResponse:
    """Enqueue a ticket for async review and return 202 Accepted."""
    db = async_db.get_db()
    job_id = await job_ops.enqueue_review(
        db=db,
        ticket_payload=ticket.model_dump(mode="json"),
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


@router.post("/webhooks/jira")
async def handle_jira_webhook(
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

    if not get_settings().support_auto_draft:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "received", "ticket_key": ticket.key, "auto_draft": False},
        )

    return await _enqueue_ticket(ticket, requested_by="webhook:jira")


@router.post("/review")
async def trigger_review(
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

    return await _enqueue_ticket(ticket, requested_by="api:manual")


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
