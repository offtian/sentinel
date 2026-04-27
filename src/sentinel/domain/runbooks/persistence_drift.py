"""
Drift event persistence for the F6.L daily sweep (``scripts/runbook_drift_check.py``).

Two operations:

* :func:`is_open_drift_recorded` — pre-write idempotency check. Returns True
  when an unresolved (``resolved_at IS NULL``) row already exists for the
  ``(runbook_id, drift_type, drift_detail)`` triple. The cron passes detected
  drift through this gate before writing so a re-running sweep on unchanged
  state is a no-op (per the F6.L spec idempotency contract).

* :func:`write_drift_event` — append a fresh
  :class:`runbook_drift.RunbookDriftHistoryRecord` row. ``detected_by`` is
  always ``"runbook_drift_check"`` since the cron is currently the sole
  writer. Future writers (a manual operator-triggered re-check, the F6.E
  pre-commit hook escalating to runtime) would pass their own actor name.

Queries use SQLAlchemy Core expressions (per the project's DB-architecture
rule: queries belong in the domain layer; no raw SQL).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from sentinel.data.sql import runbook_drift
from sentinel.domain.runbooks import drift as drift_mod
from sentinel.utils import logs


_DETECTED_BY_DEFAULT = "runbook_drift_check"


async def is_open_drift_recorded(
    *,
    session: AsyncSession,
    event: drift_mod.DriftEvent,
) -> bool:
    """
    Return True when an unresolved drift row already exists for ``event``.

    Dedup key is the natural triple ``(runbook_id, drift_type, drift_detail)``.
    ``runbook_content_sha`` is intentionally **not** part of the key — a sha
    bump indicates the runbook was edited but the drift may persist (e.g. a
    typo fix that doesn't address the missing tool name); the second sweep
    should reuse the open row, not open a parallel one.

    JSONB equality on ``drift_detail`` works because the sweeps emit
    drift_detail with stable key ordering (``ToolsYamlInvalidDetail`` sorts
    its missing names; ``StaleNoMatchesDetail`` carries deterministic ints
    derived from ``today``).

    :param session: Async SQLAlchemy session bound to the application DB.
    :param event: The :class:`drift_mod.DriftEvent` about to be persisted.
    :returns: True iff an unresolved row already covers this drift.
    """
    record_cls = runbook_drift.RunbookDriftHistoryRecord
    statement = (
        sa.select(sa.func.count())
        .select_from(record_cls)
        .where(col(record_cls.runbook_id) == event.runbook_id)
        .where(col(record_cls.drift_type) == event.drift_type)
        .where(col(record_cls.resolved_at).is_(None))
        .where(col(record_cls.drift_detail) == event.drift_detail)
    )
    result = await session.execute(statement)
    return int(result.scalar_one()) > 0


async def write_drift_event(
    *,
    session: AsyncSession,
    event: drift_mod.DriftEvent,
    detected_by: str = _DETECTED_BY_DEFAULT,
) -> uuid.UUID:
    """
    Insert one :class:`runbook_drift.RunbookDriftHistoryRecord` row for ``event``.

    Caller is responsible for the idempotency guard via
    :func:`is_open_drift_recorded`. Splitting the read and the write keeps
    this function pure-write; callers that don't want dedup (e.g. tests
    asserting the write path independently) can skip the guard.

    :param session: Async SQLAlchemy session bound to the application DB.
    :param event: The drift event to persist.
    :param detected_by: Actor identifier for the writer (defaults to the
        cron's name).
    :returns: The inserted ``drift_id`` UUID.
    """
    drift_id = uuid.uuid4()
    payload: dict[str, object] = {
        "drift_id": drift_id,
        "runbook_id": event.runbook_id,
        "runbook_content_sha": event.runbook_content_sha,
        "drift_type": event.drift_type,
        "drift_severity": event.drift_severity,
        "drift_detail": dict(event.drift_detail),
        "detected_at": datetime.now(tz=UTC),
        "detected_by": detected_by,
    }
    statement = sa.insert(runbook_drift.RunbookDriftHistoryRecord).values(**payload)
    await session.execute(statement)
    logs.log_event(
        "runbook_drift_persisted",
        params={
            "drift_id": str(drift_id),
            "runbook_id": event.runbook_id,
            "drift_type": event.drift_type,
            "drift_severity": event.drift_severity,
            "content_sha": event.runbook_content_sha,
        },
    )
    return drift_id
