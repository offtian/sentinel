from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from sentinel.domain.alerts import entities as alert_entities


class Finding(BaseModel):
    source: str
    summary: str
    raw_data: str | None = None
    relevance: float = 0.0
    # Identifiers of sources / tool calls that back this finding (RFC §5.4).
    # Populated from investigation_sources in analyse_root_cause; empty for
    # findings created before F8 or in synthetic/test contexts.
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)


class InvestigationStatus(enum.Enum):
    PENDING = "pending"
    INVESTIGATING = "investigating"
    COMPLETED = "completed"
    FAILED = "failed"


class Investigation(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    alert: alert_entities.Alert
    status: InvestigationStatus = InvestigationStatus.PENDING
    findings: list[Finding] = Field(default_factory=list)
    root_cause: str | None = None
    remediation: str | None = None
    confidence_score: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
