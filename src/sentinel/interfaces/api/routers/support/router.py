from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import fastapi

from sentinel import _config
from sentinel.domain.support import entities
from sentinel.interfaces.graphs import support_review
from sentinel.utils import logs


router = fastapi.APIRouter(prefix="/support", tags=["support"])


@router.post("/webhooks/jira")
async def handle_jira_webhook(
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """
    Receive and process Jira Service Desk webhook events.

    Jira webhooks fire on issue creation/update. We process new tickets
    and generate response suggestions.
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

    if not _config.SUPPORT_AUTO_DRAFT:
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={"status": "received", "ticket_key": ticket.key, "auto_draft": False},
        )

    result = await support_review.review_ticket(ticket=ticket)

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "status": "reviewed",
            "ticket_key": ticket.key,
            "suggested_response": result.suggested_response,
            "confidence": result.confidence.total if result.confidence else None,
            "category": result.category,
        },
    )


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

    result = await support_review.review_ticket(ticket=ticket)

    return fastapi.responses.JSONResponse(
        status_code=200,
        content={
            "status": "reviewed",
            "ticket_key": ticket.key,
            "suggested_response": result.suggested_response,
            "sources": result.sources,
            "confidence": result.confidence.total if result.confidence else None,
            "category": result.category,
        },
    )
