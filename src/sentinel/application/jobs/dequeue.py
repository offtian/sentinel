from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from sentinel.data import job_models
from sentinel.domain.jobs import entities
from sentinel.utils import logs


async def fetch_job_record(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> job_models.JobRequestRecord:
    """
    Fetch a job request record by ID within the given session.

    Use this to re-fetch a record when the original was loaded in a
    different (now-closed) session.
    """
    stmt = select(job_models.JobRequestRecord).where(col(job_models.JobRequestRecord.id) == job_id)
    result = await session.execute(stmt)
    return result.scalar_one()


async def claim_next_job(
    session: AsyncSession,
    *,
    worker_id: str,
    job_types: tuple[str, ...] = (
        entities.JobType.SRE_INVESTIGATION.value,
        entities.JobType.SUPPORT_REVIEW.value,
    ),
) -> job_models.JobRequestRecord | None:
    """
    Claim the next available job using ``SELECT ... FOR UPDATE SKIP LOCKED``.

    Returns the claimed job record with ``status='running'`` and ``locked_by``
    set to the worker ID, or ``None`` if no jobs are available.
    """
    stmt = (
        select(job_models.JobRequestRecord)
        .where(col(job_models.JobRequestRecord.status) == entities.JobStatus.PENDING.value)
        .where(col(job_models.JobRequestRecord.job_type).in_(job_types))
        .order_by(
            col(job_models.JobRequestRecord.priority).asc(),
            col(job_models.JobRequestRecord.created_at).asc(),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    result = await session.execute(stmt)
    record = result.scalar_one_or_none()

    if record is None:
        return None

    now = datetime.now(tz=UTC)
    record.status = entities.JobStatus.RUNNING.value
    record.locked_by = worker_id
    record.locked_at = now

    await session.commit()
    await session.refresh(record)

    logs.log_event(
        "job_claimed",
        params={
            "job_id": str(record.id),
            "job_type": record.job_type,
            "worker_id": worker_id,
        },
    )

    return record


async def complete_job(
    session: AsyncSession,
    *,
    job_record: job_models.JobRequestRecord,
    result_json: str | None = None,
    worker_id: str,
) -> job_models.JobResultRecord:
    """Mark a job as completed and persist the result in a single transaction."""
    now = datetime.now(tz=UTC)

    duration_ms = None
    if job_record.locked_at:
        duration_ms = int((now - job_record.locked_at).total_seconds() * 1000)

    job_record.status = entities.JobStatus.COMPLETED.value

    result_record = job_models.JobResultRecord(
        job_request_id=job_record.id,
        status=entities.JobStatus.COMPLETED.value,
        result_json=result_json,
        started_at=job_record.locked_at,
        completed_at=now,
        duration_ms=duration_ms,
        worker_id=worker_id,
    )

    session.add(result_record)
    await session.commit()
    await session.refresh(result_record)

    logs.log_event(
        "job_completed",
        params={
            "job_id": str(job_record.id),
            "duration_ms": duration_ms,
            "worker_id": worker_id,
        },
    )

    return result_record


async def fail_job(
    session: AsyncSession,
    *,
    job_record: job_models.JobRequestRecord,
    error_message: str,
    worker_id: str,
) -> job_models.JobResultRecord:
    """
    Mark a job as failed and persist the error in a single transaction.

    If the job has retries remaining, it is re-queued as pending.
    """
    now = datetime.now(tz=UTC)

    # Compute duration BEFORE clearing locked_at on the retry path.
    duration_ms = None
    if job_record.locked_at:
        duration_ms = int((now - job_record.locked_at).total_seconds() * 1000)

    should_retry = job_record.retry_count < job_record.max_retries
    if should_retry:
        job_record.status = entities.JobStatus.PENDING.value
        job_record.locked_by = None
        job_record.locked_at = None
        job_record.retry_count += 1
    else:
        job_record.status = entities.JobStatus.FAILED.value

    result_record = job_models.JobResultRecord(
        job_request_id=job_record.id,
        status=entities.JobStatus.FAILED.value,
        error_message=error_message,
        started_at=job_record.locked_at if not should_retry else None,
        completed_at=now,
        duration_ms=duration_ms,
        worker_id=worker_id,
    )

    session.add(result_record)
    await session.commit()
    await session.refresh(result_record)

    logs.log_event(
        "job_failed",
        params={
            "job_id": str(job_record.id),
            "error": error_message,
            "will_retry": should_retry,
            "retry_count": job_record.retry_count,
        },
    )

    return result_record


async def recover_stale_jobs(
    session: AsyncSession,
    *,
    worker_id: str,
) -> int:
    """
    Re-queue any jobs that were left in ``running`` state by this worker.

    Called on worker startup to recover from crashes.
    """
    stmt = (
        select(job_models.JobRequestRecord)
        .where(col(job_models.JobRequestRecord.status) == entities.JobStatus.RUNNING.value)
        .where(col(job_models.JobRequestRecord.locked_by) == worker_id)
    )

    result = await session.execute(stmt)
    stale_records = list(result.scalars().all())

    for record in stale_records:
        record.status = entities.JobStatus.PENDING.value
        record.locked_by = None
        record.locked_at = None

    if stale_records:
        await session.commit()
        logs.log_event(
            "stale_jobs_recovered",
            params={
                "worker_id": worker_id,
                "count": len(stale_records),
            },
        )

    return len(stale_records)
