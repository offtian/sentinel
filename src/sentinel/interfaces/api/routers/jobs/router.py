from __future__ import annotations

import uuid

import fastapi
from sqlmodel import col, select

from sentinel.data import database, job_models
from sentinel.interfaces.api.dependencies import require_database


router = fastapi.APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", dependencies=[fastapi.Depends(require_database)])
async def get_job_status(
    job_id: uuid.UUID,
) -> fastapi.responses.JSONResponse:
    """
    Return the current status of a job, including its result if completed.

    Returns 404 if the job ID is not found.
    """
    async with database.get_session() as session:
        # Fetch the job request
        stmt = select(job_models.JobRequestRecord).where(
            col(job_models.JobRequestRecord.id) == job_id
        )
        result = await session.execute(stmt)
        job_record = result.scalar_one_or_none()

        if job_record is None:
            return fastapi.responses.JSONResponse(
                status_code=404,
                content={"error": "Job not found", "job_id": str(job_id)},
            )

        # Fetch the latest result if any
        result_stmt = (
            select(job_models.JobResultRecord)
            .where(col(job_models.JobResultRecord.job_request_id) == job_id)
            .order_by(col(job_models.JobResultRecord.created_at).desc())
            .limit(1)
        )
        result_row = await session.execute(result_stmt)
        job_result = result_row.scalar_one_or_none()

    response: dict[str, object] = {
        "job_id": str(job_record.id),
        "job_type": job_record.job_type,
        "status": job_record.status,
        "priority": job_record.priority,
        "requested_by": job_record.requested_by,
        "created_at": job_record.created_at.isoformat(),
        "retry_count": job_record.retry_count,
    }

    if job_result:
        response["result"] = {
            "status": job_result.status,
            "started_at": job_result.started_at.isoformat() if job_result.started_at else None,
            "completed_at": (
                job_result.completed_at.isoformat() if job_result.completed_at else None
            ),
            "duration_ms": job_result.duration_ms,
            "worker_id": job_result.worker_id,
            "error_message": job_result.error_message,
        }

    return fastapi.responses.JSONResponse(status_code=200, content=response)
