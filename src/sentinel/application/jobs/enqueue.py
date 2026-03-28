from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.data import job_models
from sentinel.domain.jobs import entities
from sentinel.utils import logs


async def enqueue_job(
    session: AsyncSession,
    *,
    job_type: entities.JobType,
    payload: dict[str, Any],
    requested_by: str,
    priority: int = 1,
    source_id: str,
    max_retries: int = 3,
) -> uuid.UUID:
    """
    Enqueue a job for background processing.

    Return the job ID for tracking. If a job with the same idempotency key
    already exists, the existing job ID is returned (no duplicate created).

    :raises ValueError: if the job type is not recognised.
    """
    job_id = uuid.uuid4()
    payload_json = json.dumps(payload, default=str)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    idempotency_key = entities.make_idempotency_key(
        job_type=job_type,
        source_id=source_id,
    )

    record = job_models.JobRequestRecord(
        id=job_id,
        job_type=job_type.value,
        payload_json=payload_json,
        payload_hash=payload_hash,
        status=entities.JobStatus.PENDING.value,
        priority=priority,
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        max_retries=max_retries,
    )

    session.add(record)
    await session.commit()

    logs.log_event(
        "job_enqueued",
        params={
            "job_id": str(job_id),
            "job_type": job_type.value,
            "priority": priority,
            "requested_by": requested_by,
        },
    )

    return job_id


async def enqueue_investigation(
    session: AsyncSession,
    *,
    alert_payload: dict[str, Any],
    requested_by: str,
    alert_id: str,
    priority: int = 1,
) -> uuid.UUID:
    """
    Enqueue an SRE investigation job.

    Convenience wrapper around ``enqueue_job`` for the SRE pipeline.
    """
    return await enqueue_job(
        session,
        job_type=entities.JobType.SRE_INVESTIGATION,
        payload=alert_payload,
        requested_by=requested_by,
        source_id=alert_id,
        priority=priority,
    )


async def enqueue_review(
    session: AsyncSession,
    *,
    ticket_payload: dict[str, Any],
    requested_by: str,
    ticket_id: str,
    priority: int = 2,
) -> uuid.UUID:
    """
    Enqueue a support review job.

    Convenience wrapper around ``enqueue_job`` for the support pipeline.
    """
    return await enqueue_job(
        session,
        job_type=entities.JobType.SUPPORT_REVIEW,
        payload=ticket_payload,
        requested_by=requested_by,
        source_id=ticket_id,
        priority=priority,
    )
