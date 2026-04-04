"""
MCP server tools for triggering and querying investigations.

Uses the databases library for async PostgreSQL access.
When the database is not configured, returns fallback messages.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import databases

from sentinel.utils import logs


async def trigger_investigation(
    *,
    db: databases.Database | None,
    alert_source: str,
    alert_id: str,
    description: str = "",
) -> str:
    """
    Trigger an SRE investigation by inserting a job request.

    :param db: The async database connection (None when unconfigured).
    :param alert_source: Source of the alert (e.g. "pagerduty", "datadog").
    :param alert_id: Unique identifier for the alert.
    :param description: Optional description of the alert.
    :returns: Confirmation message with the job ID.
    """
    if db is None:
        return "Database not available. Cannot enqueue investigation."

    job_id = uuid.uuid4()
    payload = json.dumps(
        {
            "alert_source": alert_source,
            "alert_id": alert_id,
            "description": description,
        }
    )
    idempotency_key = f"mcp:sre:{alert_source}:{alert_id}"

    try:
        query = """
            INSERT INTO job_requests (
                id, job_type, payload_json, payload_hash, status,
                priority, requested_by, idempotency_key, max_retries
            ) VALUES (
                :id, :job_type, :payload_json, :payload_hash, :status,
                :priority, :requested_by, :idempotency_key, :max_retries
            )
        """
        await db.execute(
            query=query,
            values={
                "id": job_id,
                "job_type": "SRE_INVESTIGATION",
                "payload_json": payload,
                "payload_hash": str(uuid.uuid4())[:16],
                "status": "pending",
                "priority": 1,
                "requested_by": f"mcp:{alert_source}",
                "idempotency_key": idempotency_key,
                "max_retries": 3,
            },
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "trigger_investigation", "alert_id": alert_id})
        return f"Failed to enqueue investigation: {type(exc).__name__}"

    return f"Investigation triggered. job_id={job_id} alert={alert_source}/{alert_id}"


async def get_investigation_status(
    *,
    db: databases.Database | None,
    investigation_id: str,
) -> str:
    """
    Check the status of a job request by ID.

    :param db: The async database connection (None when unconfigured).
    :param investigation_id: The job request UUID.
    :returns: Status message with job details.
    """
    if db is None:
        return "Database not available. Cannot check investigation status."

    try:
        query = """
            SELECT id, status, job_type, created_at
            FROM job_requests
            WHERE id = :id
        """
        row = await db.fetch_one(query=query, values={"id": investigation_id})
    except Exception as exc:
        logs.log_exception(
            exc, params={"tool": "get_investigation_status", "id": investigation_id}
        )
        return f"Status lookup failed: {type(exc).__name__}"

    if row is None:
        return f"Investigation {investigation_id} not found."

    row_dict: dict[str, Any] = dict(row)
    status = row_dict.get("status", "unknown")
    job_type = row_dict.get("job_type", "unknown")
    created = row_dict.get("created_at", "")

    return f"Investigation {investigation_id}: status={status}, type={job_type}, created={created}"
