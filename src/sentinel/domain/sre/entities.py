from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AlertSeverity(enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Alert(BaseModel):
    id: str
    source: Literal["pagerduty", "datadog"]
    title: str
    description: str
    severity: AlertSeverity
    service: str
    triggered_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    source: str
    summary: str
    raw_data: str | None = None
    relevance: float = 0.0


class InvestigationStatus(enum.Enum):
    PENDING = "pending"
    INVESTIGATING = "investigating"
    COMPLETED = "completed"
    FAILED = "failed"


class Investigation(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    alert: Alert
    status: InvestigationStatus = InvestigationStatus.PENDING
    findings: list[Finding] = Field(default_factory=list)
    root_cause: str | None = None
    remediation: str | None = None
    confidence_score: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
