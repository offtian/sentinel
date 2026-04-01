from __future__ import annotations

from typing import Any

import fastapi

from sentinel.application.automations import runner
from sentinel.application.jobs import enqueue
from sentinel.data import database
from sentinel.interfaces.api.dependencies import require_database
from sentinel.utils import logs


router = fastapi.APIRouter(prefix="/automations", tags=["automations"])


@router.post("/trigger", dependencies=[fastapi.Depends(require_database)])
async def trigger_automation(
    payload: dict[str, Any],
) -> fastapi.responses.JSONResponse:
    """
    Manually trigger a scheduled automation.

    Enqueues the automation for background execution by the worker.

    Expects:
    {
        "automation_name": "repo_health_check",
        "params": {"repos": ["sentinel"]}
    }
    """
    automation_name = payload.get("automation_name", "")
    params = payload.get("params", {})

    if not automation_name:
        return fastapi.responses.JSONResponse(
            status_code=400,
            content={"error": "automation_name is required"},
        )

    available = runner.list_automations()
    if automation_name not in available:
        return fastapi.responses.JSONResponse(
            status_code=400,
            content={
                "error": f"Unknown automation: {automation_name}",
                "available": available,
            },
        )

    async with database.get_session() as session:
        job_id = await enqueue.enqueue_automation(
            session,
            automation_name=automation_name,
            params=params,
            requested_by="api:manual",
        )

    logs.log_event(
        "automation_triggered",
        params={"automation_name": automation_name, "job_id": str(job_id)},
    )

    return fastapi.responses.JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": str(job_id),
            "automation_name": automation_name,
        },
    )


@router.get("/available")
async def list_available_automations() -> fastapi.responses.JSONResponse:
    """Return the list of registered automation names."""
    return fastapi.responses.JSONResponse(
        status_code=200,
        content={"automations": runner.list_automations()},
    )
