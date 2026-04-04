"""
Append-only audit log write operations.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import databases
from sqlalchemy import insert

from sentinel.data import audit_models
from sentinel.utils import logs


async def record_audit_entry(
    *,
    db: databases.Database,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any],
    input_hash: str,
    model_id: str = "",
    prompt_version: str = "",
) -> uuid.UUID:
    """
    Insert an append-only audit log entry.

    :param db: The async database connection.
    :param actor: Identity of the actor performing the action (e.g. "system", "user-42").
    :param action: Name of the action being audited (e.g. "investigate", "approve").
    :param resource_type: Type of resource the action applies to (e.g. "alert", "investigation").
    :param resource_id: Identifier of the resource in its source system.
    :param details: Arbitrary dict of additional context; serialized to JSON for storage.
    :param input_hash: SHA-256 hex digest of the normalized input payload.
    :param model_id: Optional LLM model identifier used during the action.
    :param prompt_version: Optional prompt template version used during the action.
    :returns: The UUID of the inserted audit log row.
    """
    row_id = uuid.uuid4()
    timestamp = datetime.now(tz=UTC)
    details_json = json.dumps(details, default=str)
    query = insert(audit_models.AuditLogRecord).values(
        id=row_id,
        timestamp=timestamp,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details_json=details_json,
        input_hash=input_hash,
        model_id=model_id,
        prompt_version=prompt_version,
    )
    await db.execute(query)
    logs.log_event(
        "audit_entry_recorded",
        params={
            "record_id": str(row_id),
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
    )
    return row_id
