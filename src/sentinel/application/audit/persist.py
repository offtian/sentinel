from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.data import audit_models
from sentinel.utils import logs


async def record_audit_entry(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any],
    input_hash: str,
    model_id: str = "",
    prompt_version: str = "",
) -> audit_models.AuditLogRecord:
    """
    Append an immutable audit entry to the audit log.

    This is the sole write path for audit data. The record is committed
    immediately so that audit entries survive even if the surrounding
    operation fails.
    """
    record = audit_models.AuditLogRecord(
        id=uuid.uuid4(),
        timestamp=datetime.now(tz=UTC),
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details_json=json.dumps(details, default=str),
        input_hash=input_hash,
        model_id=model_id,
        prompt_version=prompt_version,
    )

    session.add(record)
    await session.commit()

    logs.log_event(
        "audit_entry_recorded",
        params={
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
    )

    return record
