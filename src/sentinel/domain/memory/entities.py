"""
Frozen entities for the long-term incident memory domain.

:class:`IncidentMemory` carries the full denormalised payload — what the
``persist`` path inserts and what the recall path projects rows back into.
:class:`SimilarIncident` is the recall-result projection: a memory plus the
similarity score and the section that produced the match, ready for the
analyser prompt to render.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import attrs


@attrs.frozen(kw_only=True, slots=True)
class IncidentMemory:
    """
    A long-term memory of one resolved investigation, scoped to a fund + cluster.

    Mirrors the row shape of
    :class:`sentinel.data.sql.incident_memory.IncidentMemoryRecord` but in
    pure-Python form so callers above the data layer never see a SQLModel.
    """

    memory_id: uuid.UUID
    tenant_id: str
    cluster_id: str
    service: str
    alert_signature: str
    alert_title: str
    alert_description: str
    root_cause: str
    remediation: str
    confidence_score: float
    source_investigation_id: uuid.UUID
    occurred_at: datetime


@attrs.frozen(kw_only=True, slots=True)
class SimilarIncident:
    """
    A single recall hit: a prior :class:`IncidentMemory` plus the similarity
    score and the embedded section that produced the match.

    ``similarity`` is cosine similarity in ``[-1, 1]`` (1.0 = identical,
    0.0 = orthogonal); the section is one of ``"alert"``, ``"root_cause"``,
    ``"remediation"`` so the prompt can flag *why* a prior incident looked
    similar (e.g. matched on remediation rather than alert text).
    """

    memory: IncidentMemory
    similarity: float
    matched_section: str


__all__ = ("IncidentMemory", "SimilarIncident")
