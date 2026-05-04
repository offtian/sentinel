"""
Write-side operations for the long-term incident memory store.

Pure-insert helpers — the embedder call lives in :mod:`embeddings` so a
transient embedder failure cannot block the row write. Callers (the
``publish_findings`` node) are expected to wrap both calls in a single
try/except that logs but never raises: memory is best-effort, never
blocks the publish path.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.data.sql import incident_memory as memory_records
from sentinel.domain.memory import entities as memory_entities
from sentinel.utils import logs


def compute_alert_signature(
    *,
    labels: Iterable[str],
    classification_category: str,
) -> str:
    """
    Return ``sha256(sorted(labels) || classification_category)[:16]``.

    Same fingerprint convention as
    :class:`sentinel.data.sql.runbook_gap_cluster.RunbookGapClusterRecord` so
    incident memory and gap clusters can be cross-referenced by signature
    when future surfaces (e.g. an admin UI) want to show "this gap pattern's
    historical resolutions".

    The labels are sorted before hashing so the fingerprint is order-stable
    across alerters that emit labels in non-deterministic order.
    """
    sorted_labels = sorted(labels)
    payload = "\n".join(sorted_labels) + "\n" + classification_category
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:16]


async def persist_incident_memory(
    *,
    session: AsyncSession,
    tenant_id: str,
    cluster_id: str,
    service: str,
    alert_signature: str,
    alert_title: str,
    alert_description: str,
    root_cause: str,
    remediation: str,
    confidence_score: float,
    source_investigation_id: uuid.UUID,
    occurred_at: datetime,
) -> memory_entities.IncidentMemory:
    """
    Insert one ``incident_memory`` row and return the in-memory entity.

    The returned :class:`IncidentMemory` carries the freshly minted
    ``memory_id`` so the caller can immediately hand it to
    :func:`sentinel.domain.memory.embeddings.index_incident_memory` without
    a follow-up SELECT.

    :raises sqlalchemy.exc.SQLAlchemyError: bubbles up on insert failure;
        callers are expected to wrap in their own try/except per the
        best-effort contract.
    """
    memory_id = uuid.uuid4()
    statement = insert(memory_records.IncidentMemoryRecord).values(
        memory_id=memory_id,
        tenant_id=tenant_id,
        cluster_id=cluster_id,
        service=service,
        alert_signature=alert_signature,
        alert_title=alert_title,
        alert_description=alert_description,
        root_cause=root_cause,
        remediation=remediation,
        confidence_score=confidence_score,
        source_investigation_id=source_investigation_id,
        occurred_at=occurred_at,
    )
    await session.execute(statement)
    logs.log_event(
        "incident_memory_persisted",
        params={
            "memory_id": str(memory_id),
            "tenant_id": tenant_id,
            "cluster_id": cluster_id,
            "alert_signature": alert_signature,
            "source_investigation_id": str(source_investigation_id),
            "confidence_score": confidence_score,
        },
    )
    return memory_entities.IncidentMemory(
        memory_id=memory_id,
        tenant_id=tenant_id,
        cluster_id=cluster_id,
        service=service,
        alert_signature=alert_signature,
        alert_title=alert_title,
        alert_description=alert_description,
        root_cause=root_cause,
        remediation=remediation,
        confidence_score=confidence_score,
        source_investigation_id=source_investigation_id,
        occurred_at=occurred_at,
    )


__all__ = ("compute_alert_signature", "persist_incident_memory")
