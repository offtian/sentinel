"""
F8 — Audit trail writer for pipeline state transitions (R-CO-1).

Each investigation state transition is recorded as an append-only row in
``audit_log``. The Postgres WORM trigger (from F3.6) computes ``row_hash``
server-side. ``prev_hash`` is set to ``None`` for foundations (first-row
semantics); a follow-on plan will introduce the full prev_hash chaining
query once the audit volume warrants it.

Public API:
  record_transition(*, request_id, from_state, to_state, reason, db_session)

The function is fire-and-forget from the pipeline's perspective: a failed
write logs the exception and returns ``None`` so the pipeline continues.
Callers that do not have a DB session (e.g. unit tests) pass ``None`` and
the function is a no-op.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.data.sql import audit as audit_sql
from sentinel.utils import logs


async def record_transition(
    *,
    request_id: uuid.UUID,
    from_state: str,
    to_state: str,
    reason: str,
    db_session: AsyncSession | None,
) -> None:
    """
    Append one state-transition row to ``audit_log`` for R-CO-1 compliance.

    :param request_id: The investigation's request UUID (FK to ``alert_request``).
    :param from_state: Human-readable name of the state being left.
    :param to_state: Human-readable name of the state being entered.
    :param reason: Short phrase describing why the transition occurred.
    :param db_session: An open async SQLAlchemy session. When ``None`` the
        call is a no-op so callers without DB connectivity (tests, replay)
        do not need to special-case it.
    """
    if db_session is None:
        return

    try:
        record = audit_sql.AuditLogRecord(
            actor="pipeline",
            action=to_state,
            resource_type="investigation",
            resource_id=str(request_id),
            details_json=json.dumps({"from": from_state, "reason": reason}),
            input_hash="",
            request_id=request_id,
            prev_hash=None,
            timestamp=datetime.now(tz=UTC),
        )
        db_session.add(record)
        await db_session.flush()
    except Exception as exc:
        logs.log_exception(
            exc,
            params={
                "request_id": str(request_id),
                "from_state": from_state,
                "to_state": to_state,
                "node": "audit.record_transition",
            },
        )
