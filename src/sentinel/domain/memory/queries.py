"""
Read-side queries for the long-term incident memory store.

These queries are scoped strictly by ``(tenant_id, cluster_id)`` so funds
never see each other's history — the same multi-tenant invariant that
applies to every other read path in the platform (RFC §3.1).

:func:`fetch_recent_for_cluster` is the embedder-unavailable fallback for
:func:`sentinel.domain.memory.embeddings.retrieve_similar_incidents`: when
the embedder transport is down, we degrade to a recency-based recall
rather than blocking the analyser. Pure SQL — no embedding required.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from sentinel.data.sql import incident_memory as memory_records
from sentinel.domain.memory import entities as memory_entities


async def fetch_recent_for_cluster(
    *,
    session: AsyncSession,
    tenant_id: str,
    cluster_id: str,
    now: datetime,
    limit: int = 10,
    recency_window: timedelta = timedelta(days=90),
) -> tuple[memory_entities.IncidentMemory, ...]:
    """
    Return the most recent memories for ``(tenant_id, cluster_id)``.

    Ordered by ``occurred_at DESC`` — the same predicate the
    ``ix_incident_memory_tenant_cluster_occurred`` index serves directly.
    The recency window defaults to 90 days; older memories are deliberately
    excluded so the analyser never sees stale patterns from a long-gone
    cluster topology.

    :param now: Tz-aware UTC ``datetime`` from the caller. Per
        ``application.md``, the system clock lives at the interface layer —
        domain queries take a ``now`` parameter rather than calling
        ``datetime.now()``.
    """
    cutoff = now - recency_window
    statement = (
        sa.select(memory_records.IncidentMemoryRecord)
        .where(col(memory_records.IncidentMemoryRecord.tenant_id) == tenant_id)
        .where(col(memory_records.IncidentMemoryRecord.cluster_id) == cluster_id)
        .where(col(memory_records.IncidentMemoryRecord.occurred_at) >= cutoff)
        .order_by(col(memory_records.IncidentMemoryRecord.occurred_at).desc())
        .limit(limit)
    )
    result = await session.execute(statement)
    rows = result.scalars().all()
    return tuple(_record_to_entity(record) for record in rows)


async def fetch_by_signature(
    *,
    session: AsyncSession,
    tenant_id: str,
    cluster_id: str,
    alert_signature: str,
    limit: int = 5,
) -> tuple[memory_entities.IncidentMemory, ...]:
    """
    Return memories with an exact ``alert_signature`` match for this scope.

    Backed by ``ix_incident_memory_signature``. Ordered by ``occurred_at
    DESC`` so the most recent identical-fingerprint resolution comes first
    — the canonical "we have hit this exact gap before" lookup.
    """
    statement = (
        sa.select(memory_records.IncidentMemoryRecord)
        .where(col(memory_records.IncidentMemoryRecord.tenant_id) == tenant_id)
        .where(col(memory_records.IncidentMemoryRecord.cluster_id) == cluster_id)
        .where(col(memory_records.IncidentMemoryRecord.alert_signature) == alert_signature)
        .order_by(col(memory_records.IncidentMemoryRecord.occurred_at).desc())
        .limit(limit)
    )
    result = await session.execute(statement)
    rows = result.scalars().all()
    return tuple(_record_to_entity(record) for record in rows)


def _record_to_entity(
    record: memory_records.IncidentMemoryRecord,
) -> memory_entities.IncidentMemory:
    """Project a SQLModel row into a frozen :class:`IncidentMemory`."""
    return memory_entities.IncidentMemory(
        memory_id=record.memory_id,
        tenant_id=record.tenant_id,
        cluster_id=record.cluster_id,
        service=record.service,
        alert_signature=record.alert_signature,
        alert_title=record.alert_title,
        alert_description=record.alert_description,
        root_cause=record.root_cause,
        remediation=record.remediation,
        confidence_score=record.confidence_score,
        source_investigation_id=record.source_investigation_id,
        occurred_at=record.occurred_at,
    )


__all__ = ("fetch_by_signature", "fetch_recent_for_cluster")
