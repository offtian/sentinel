"""
Embedding-based retrieval for the long-term incident memory store.

Direct adaptation of :mod:`sentinel.domain.runbooks.rag` — same
:class:`Embedder` Protocol (re-exported from the runbook module so there
is exactly one definition), same pgvector ``<=>`` cosine-distance query,
same vector-literal trick for asyncpg compatibility.

Two operations:

* :func:`index_incident_memory` writes the three section embeddings
  (``alert``, ``root_cause``, ``remediation``) for a freshly persisted
  memory. Idempotent on the unique identity tuple.
* :func:`retrieve_similar_incidents` embeds a query and returns the top-k
  similar memories scoped to ``(tenant_id, cluster_id)``. Filters by an
  ``occurred_at`` recency window so stale patterns drop out, and by a
  ``min_similarity`` floor so the analyser never sees noise.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from sentinel.data.sql import incident_memory_embeddings as embedding_records
from sentinel.domain.memory import entities as memory_entities
from sentinel.domain.runbooks import rag as runbook_rag
from sentinel.utils import logs


def _sha256_truncated(text: str) -> str:
    """Return ``sha256(text)[:32]``, mirroring the runbook embedder convention."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest[:32]


def _vector_literal(embedding: Sequence[float]) -> str:
    """
    Return the pgvector text literal form of ``embedding``.

    Same encoding as :func:`sentinel.domain.runbooks.rag._vector_literal`,
    inlined here so the memory module does not reach into the runbooks
    package's private helpers.
    """
    return "[" + ",".join(f"{value:.7f}" for value in embedding) + "]"


# Re-export the runbook embedder Protocol + unavailable error so callers
# above the data layer have a single ``Embedder`` import surface and the
# matcher's failure-mode contract carries over unchanged.
Embedder = runbook_rag.Embedder
EmbedderUnavailableError = runbook_rag.EmbedderUnavailableError


# Section names mirror the ``IncidentEmbeddingSection`` Literal in the SQLModel.
_INDEXED_SECTIONS: tuple[str, ...] = ("alert", "root_cause", "remediation")


# Length cap on each embedded section. Same ceiling as the runbook embedder
# — embedders cap input around 8k tokens; 32k chars leaves headroom for the
# tokeniser overhead.
_MAX_SECTION_CHARS: int = 32_000


def _section_text(*, memory: memory_entities.IncidentMemory, section: str) -> str:
    """Return the canonical text fed to the embedder for ``section``."""
    if section == "alert":
        return f"{memory.alert_title}\n\n{memory.alert_description}".strip()
    if section == "root_cause":
        return memory.root_cause.strip()
    if section == "remediation":
        return memory.remediation.strip()
    msg = f"unknown embedding section: {section!r}"
    raise ValueError(msg)


def _truncate_for_embedder(text: str) -> str:
    """Cap ``text`` at :data:`_MAX_SECTION_CHARS`."""
    if len(text) <= _MAX_SECTION_CHARS:
        return text
    return text[:_MAX_SECTION_CHARS]


async def _row_already_indexed(
    *,
    session: AsyncSession,
    memory_id: object,
    section: str,
    model_id: str,
    model_version: str,
) -> bool:
    """Return True when an embedding row already exists for the identity tuple."""
    record = embedding_records.IncidentMemoryEmbeddingRecord
    stmt = sa.select(sa.literal(1)).where(
        col(record.memory_id) == memory_id,
        col(record.embedding_section) == section,
        col(record.embedding_model) == model_id,
        col(record.embedding_model_ver) == model_version,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def index_incident_memory(
    *,
    session: AsyncSession,
    memory: memory_entities.IncidentMemory,
    embedder: Embedder,
) -> None:
    """
    Index ``memory``'s three sections (alert / root_cause / remediation).

    Idempotent: skips any section row that already exists for the unique
    identity ``(memory_id, section, model_id, model_version)``. Empty
    sections are also skipped.

    :raises EmbedderUnavailableError: bubbles up from the embedder. The
        ``publish_findings`` caller catches this and logs without raising
        — memory indexing is best-effort.
    """
    indexed_sections: list[str] = []
    for section in _INDEXED_SECTIONS:
        raw_text = _section_text(memory=memory, section=section)
        if not raw_text:
            continue
        text = _truncate_for_embedder(raw_text)
        already_indexed = await _row_already_indexed(
            session=session,
            memory_id=memory.memory_id,
            section=section,
            model_id=embedder.model_id,
            model_version=embedder.model_version,
        )
        if already_indexed:
            continue
        embedding = await embedder.embed(text)
        record = embedding_records.IncidentMemoryEmbeddingRecord(
            memory_id=memory.memory_id,
            embedding_section=section,
            embedding_model=embedder.model_id,
            embedding_model_ver=embedder.model_version,
            embedding_dim=len(embedding),
            embedding=list(embedding),
            source_text=text,
            source_text_sha=_sha256_truncated(text),
        )
        session.add(record)
        indexed_sections.append(section)
    if indexed_sections:
        await session.flush()
        logs.log_event(
            "incident_memory_embeddings_indexed",
            params={
                "memory_id": str(memory.memory_id),
                "tenant_id": memory.tenant_id,
                "cluster_id": memory.cluster_id,
                "sections": indexed_sections,
                "model_id": embedder.model_id,
                "model_version": embedder.model_version,
            },
        )


async def retrieve_similar_incidents(
    *,
    session: AsyncSession,
    tenant_id: str,
    cluster_id: str,
    query_text: str,
    embedder: Embedder,
    now: datetime,
    k: int = 3,
    min_similarity: float = 0.78,
    recency_window: timedelta = timedelta(days=90),
) -> tuple[memory_entities.SimilarIncident, ...]:
    """
    Embed ``query_text`` and return up to ``k`` similar memories.

    Strictly scoped by ``(tenant_id, cluster_id)`` — the JOIN to
    ``incident_memory`` filters by both before the ANN sort so funds
    never see each other's history. Also filters by:

    * ``embedding_model`` + ``embedding_model_ver`` so multi-model rows
      in the same table never cross-contaminate.
    * ``occurred_at >= now - recency_window`` so stale patterns drop out.
    * ``similarity >= min_similarity`` so the analyser never sees noise.

    Per-memory de-duplication: a memory may have up to three section
    embeddings, but only the highest-similarity section per memory is
    returned, keeping the prompt rendering one-row-per-incident.

    :param now: Tz-aware UTC ``datetime``. Domain queries take ``now`` as
        a parameter (``application.md``: minimise system-clock calls in the
        domain layer).

    :raises EmbedderUnavailableError: bubbles up from the embedder. The
        ``analyse_root_cause`` caller catches this and falls back to
        :func:`sentinel.domain.memory.queries.fetch_recent_for_cluster`.
    """
    cutoff = now - recency_window
    query_embedding = await embedder.embed(query_text)
    # pgvector's ``<=>`` operator returns cosine distance; we project
    # ``1 - distance`` as similarity. The JOIN + WHERE ordering matters:
    # filter by tenant/cluster/recency BEFORE the ANN sort so the HNSW
    # index never sees a candidate it would later have to discard.
    stmt = sa.text(
        "SELECT m.memory_id, m.tenant_id, m.cluster_id, m.service, "
        "m.alert_signature, m.alert_title, m.alert_description, "
        "m.root_cause, m.remediation, m.confidence_score, "
        "m.source_investigation_id, m.occurred_at, "
        "e.embedding_section, "
        "1 - (e.embedding <=> CAST(:query_embedding AS vector)) AS similarity "
        "FROM incident_memory_embeddings e "
        "JOIN incident_memory m ON m.memory_id = e.memory_id "
        "WHERE m.tenant_id = :tenant_id "
        "  AND m.cluster_id = :cluster_id "
        "  AND m.occurred_at >= :cutoff "
        "  AND e.embedding_model = :model "
        "  AND e.embedding_model_ver = :model_ver "
        "ORDER BY e.embedding <=> CAST(:query_embedding AS vector) "
        "LIMIT :limit"
    )
    # Over-fetch by a factor so per-memory dedup still leaves us with k
    # winners: with three sections per memory worst-case, fetch 3*k.
    fetch_limit = k * len(_INDEXED_SECTIONS)
    result = await session.execute(
        stmt,
        {
            "query_embedding": _vector_literal(query_embedding),
            "tenant_id": tenant_id,
            "cluster_id": cluster_id,
            "cutoff": cutoff,
            "model": embedder.model_id,
            "model_ver": embedder.model_version,
            "limit": fetch_limit,
        },
    )
    rows = result.all()
    best_per_memory: dict[object, memory_entities.SimilarIncident] = {}
    for row in rows:
        similarity_float = float(row.similarity)
        if similarity_float < min_similarity:
            continue
        memory = memory_entities.IncidentMemory(
            memory_id=row.memory_id,
            tenant_id=row.tenant_id,
            cluster_id=row.cluster_id,
            service=row.service,
            alert_signature=row.alert_signature,
            alert_title=row.alert_title,
            alert_description=row.alert_description,
            root_cause=row.root_cause,
            remediation=row.remediation,
            confidence_score=row.confidence_score,
            source_investigation_id=row.source_investigation_id,
            occurred_at=row.occurred_at,
        )
        candidate = memory_entities.SimilarIncident(
            memory=memory,
            similarity=similarity_float,
            matched_section=str(row.embedding_section),
        )
        prior = best_per_memory.get(memory.memory_id)
        if prior is None or candidate.similarity > prior.similarity:
            best_per_memory[memory.memory_id] = candidate
    sorted_winners = sorted(
        best_per_memory.values(),
        key=lambda hit: -hit.similarity,
    )
    return tuple(sorted_winners[:k])


__all__ = (
    "Embedder",
    "EmbedderUnavailableError",
    "index_incident_memory",
    "retrieve_similar_incidents",
)
