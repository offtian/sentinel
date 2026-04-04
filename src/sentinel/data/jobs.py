"""
Job queue persistence via the databases library.

Uses PostgreSQL ``SELECT ... FOR UPDATE SKIP LOCKED`` for safe concurrent
worker claiming.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import databases

from sentinel.utils import logs


async def enqueue_job(
    *,
    db: databases.Database,
    job_type: str,
    payload: dict[str, Any],
    requested_by: str,
    source_id: str,
    priority: int = 1,
    max_retries: int = 3,
    trace_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Insert a job request into the queue.

    :param db: The async database connection.
    :param job_type: Type of job (e.g. "sre_investigation").
    :param payload: Arbitrary job payload dict.
    :param requested_by: Identifier for who/what requested the job.
    :param source_id: Source identifier used for idempotency.
    :param priority: Job priority (lower = higher priority).
    :param max_retries: Maximum retry attempts on failure.
    :param trace_id: Optional trace correlation UUID.
    :returns: The UUID of the inserted job request.
    """
    job_id = uuid.uuid4()
    created_at = datetime.now(tz=UTC)
    payload_json = json.dumps(payload, default=str)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    idempotency_key = hashlib.sha256(f"{job_type}:{source_id}".encode()).hexdigest()

    query = """
        INSERT INTO job_requests (
            id, job_type, payload_json, payload_hash, status, priority,
            requested_by, idempotency_key, retry_count, max_retries,
            created_at, trace_id
        ) VALUES (
            :id, :job_type, :payload_json, :payload_hash, :status, :priority,
            :requested_by, :idempotency_key, :retry_count, :max_retries,
            :created_at, :trace_id
        )
    """
    await db.execute(
        query=query,
        values={
            "id": job_id,
            "job_type": job_type,
            "payload_json": payload_json,
            "payload_hash": payload_hash,
            "status": "pending",
            "priority": priority,
            "requested_by": requested_by,
            "idempotency_key": idempotency_key,
            "retry_count": 0,
            "max_retries": max_retries,
            "created_at": created_at,
            "trace_id": trace_id,
        },
    )
    logs.log_event(
        "job_enqueued",
        params={
            "job_id": str(job_id),
            "job_type": job_type,
            "priority": priority,
            "requested_by": requested_by,
        },
    )
    return job_id


async def enqueue_investigation(
    *,
    db: databases.Database,
    alert_payload: dict[str, Any],
    requested_by: str,
    alert_id: str,
    priority: int = 1,
    trace_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Enqueue an SRE investigation job.

    Convenience wrapper around ``enqueue_job`` with job_type="sre_investigation".

    :param db: The async database connection.
    :param alert_payload: Alert data payload.
    :param requested_by: Identifier for the requester.
    :param alert_id: Alert identifier used for idempotency.
    :param priority: Job priority (default 1).
    :param trace_id: Optional trace correlation UUID.
    :returns: The UUID of the inserted job request.
    """
    return await enqueue_job(
        db=db,
        job_type="sre_investigation",
        payload=alert_payload,
        requested_by=requested_by,
        source_id=alert_id,
        priority=priority,
        trace_id=trace_id,
    )


async def enqueue_review(
    *,
    db: databases.Database,
    ticket_payload: dict[str, Any],
    requested_by: str,
    ticket_id: str,
    priority: int = 2,
    trace_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Enqueue a support review job.

    Convenience wrapper around ``enqueue_job`` with job_type="support_review".

    :param db: The async database connection.
    :param ticket_payload: Ticket data payload.
    :param requested_by: Identifier for the requester.
    :param ticket_id: Ticket identifier used for idempotency.
    :param priority: Job priority (default 2).
    :param trace_id: Optional trace correlation UUID.
    :returns: The UUID of the inserted job request.
    """
    return await enqueue_job(
        db=db,
        job_type="support_review",
        payload=ticket_payload,
        requested_by=requested_by,
        source_id=ticket_id,
        priority=priority,
        trace_id=trace_id,
    )


async def enqueue_automation(
    *,
    db: databases.Database,
    automation_name: str,
    params: dict[str, Any] | None = None,
    requested_by: str,
    priority: int = 2,
) -> uuid.UUID:
    """
    Enqueue a scheduled automation job.

    Convenience wrapper around ``enqueue_job`` with
    job_type="scheduled_automation".

    :param db: The async database connection.
    :param automation_name: Name of the automation to run.
    :param params: Optional parameters for the automation.
    :param requested_by: Identifier for the requester.
    :param priority: Job priority (default 2).
    :returns: The UUID of the inserted job request.
    """
    return await enqueue_job(
        db=db,
        job_type="scheduled_automation",
        payload={
            "automation_name": automation_name,
            "params": params or {},
        },
        requested_by=requested_by,
        source_id=f"automation:{automation_name}",
        priority=priority,
    )


async def claim_next_job(
    *,
    db: databases.Database,
    worker_id: str,
    job_types: tuple[str, ...] = ("sre_investigation", "support_review"),
) -> dict[str, Any] | None:
    """
    Claim the next available job using ``SELECT ... FOR UPDATE SKIP LOCKED``.

    :param db: The async database connection.
    :param worker_id: Identifier for the claiming worker.
    :param job_types: Tuple of job types to consider.
    :returns: The claimed job row as a dict, or None if no jobs available.
    """
    placeholders = ", ".join(f":jt{i}" for i in range(len(job_types)))
    type_values = {f"jt{i}": jt for i, jt in enumerate(job_types)}

    select_query = f"""
        SELECT *
        FROM job_requests
        WHERE status = 'pending'
          AND job_type IN ({placeholders})
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    """  # noqa: S608
    row = await db.fetch_one(query=select_query, values=type_values)

    if row is None:
        return None

    row_dict = dict(row)
    job_id = row_dict["id"]
    now = datetime.now(tz=UTC)

    update_query = """
        UPDATE job_requests
        SET status = 'running', locked_by = :locked_by, locked_at = :locked_at
        WHERE id = :id
    """
    await db.execute(
        query=update_query,
        values={
            "id": job_id,
            "locked_by": worker_id,
            "locked_at": now,
            "status": "running",
        },
    )

    logs.log_event(
        "job_claimed",
        params={
            "job_id": str(job_id),
            "job_type": row_dict.get("job_type", ""),
            "worker_id": worker_id,
        },
    )

    return row_dict


async def fetch_job(
    *,
    db: databases.Database,
    job_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch a single job request by its primary key.

    :param db: The async database connection.
    :param job_id: UUID primary key of the job request.
    :returns: Row dict if found, or None.
    """
    query = """
        SELECT *
        FROM job_requests
        WHERE id = :id
    """
    row = await db.fetch_one(query=query, values={"id": job_id})
    if row is None:
        return None
    return dict(row)


async def complete_job(
    *,
    db: databases.Database,
    job_id: uuid.UUID,
    result_json: str | None = None,
    worker_id: str,
) -> uuid.UUID:
    """
    Mark a job as completed and persist the result record.

    :param db: The async database connection.
    :param job_id: UUID of the job request to complete.
    :param result_json: Optional JSON string with result data.
    :param worker_id: Identifier for the worker that completed the job.
    :returns: The UUID of the inserted job result.
    """
    now = datetime.now(tz=UTC)

    update_query = """
        UPDATE job_requests
        SET status = 'completed'
        WHERE id = :id
    """
    await db.execute(
        query=update_query,
        values={"id": job_id},
    )

    result_id = uuid.uuid4()
    insert_query = """
        INSERT INTO job_results (
            id, job_request_id, status, result_json, completed_at,
            worker_id, created_at
        ) VALUES (
            :id, :job_request_id, :status, :result_json, :completed_at,
            :worker_id, :created_at
        )
    """
    await db.execute(
        query=insert_query,
        values={
            "id": result_id,
            "job_request_id": job_id,
            "status": "completed",
            "result_json": result_json,
            "completed_at": now,
            "worker_id": worker_id,
            "created_at": now,
        },
    )

    logs.log_event(
        "job_completed",
        params={
            "job_id": str(job_id),
            "result_id": str(result_id),
            "worker_id": worker_id,
        },
    )

    return result_id


async def fail_job(
    *,
    db: databases.Database,
    job_id: uuid.UUID,
    error_message: str,
    worker_id: str,
    should_retry: bool = False,
) -> uuid.UUID:
    """
    Mark a job as failed and persist the error record.

    If ``should_retry`` is True, the job is reset to pending with an
    incremented retry count. Otherwise it is marked as failed.

    :param db: The async database connection.
    :param job_id: UUID of the job request that failed.
    :param error_message: Description of the failure.
    :param worker_id: Identifier for the worker that ran the job.
    :param should_retry: Whether to re-queue the job for retry.
    :returns: The UUID of the inserted job result.
    """
    now = datetime.now(tz=UTC)

    if should_retry:
        update_query = """
            UPDATE job_requests
            SET status = 'pending',
                locked_by = NULL,
                locked_at = NULL,
                retry_count = retry_count + 1
            WHERE id = :id
        """
    else:
        update_query = """
            UPDATE job_requests
            SET status = 'failed'
            WHERE id = :id
        """

    await db.execute(
        query=update_query,
        values={"id": job_id},
    )

    result_id = uuid.uuid4()
    insert_query = """
        INSERT INTO job_results (
            id, job_request_id, status, error_message, completed_at,
            worker_id, created_at
        ) VALUES (
            :id, :job_request_id, :status, :error_message, :completed_at,
            :worker_id, :created_at
        )
    """
    await db.execute(
        query=insert_query,
        values={
            "id": result_id,
            "job_request_id": job_id,
            "status": "failed",
            "error_message": error_message,
            "completed_at": now,
            "worker_id": worker_id,
            "created_at": now,
        },
    )

    logs.log_event(
        "job_failed",
        params={
            "job_id": str(job_id),
            "error": error_message,
            "will_retry": should_retry,
            "worker_id": worker_id,
        },
    )

    return result_id


async def recover_stale_jobs(
    *,
    db: databases.Database,
    worker_id: str,
) -> None:
    """
    Re-queue any jobs left in ``running`` state by the given worker.

    Called on worker startup to recover from crashes.

    :param db: The async database connection.
    :param worker_id: Identifier of the worker whose stale jobs to recover.
    """
    query = """
        UPDATE job_requests
        SET status = 'pending', locked_by = NULL, locked_at = NULL
        WHERE status = 'running' AND locked_by = :worker_id
    """
    await db.execute(
        query=query,
        values={"worker_id": worker_id},
    )

    logs.log_event(
        "stale_jobs_recovered",
        params={"worker_id": worker_id},
    )
