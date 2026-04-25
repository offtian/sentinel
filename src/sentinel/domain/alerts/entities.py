from __future__ import annotations

import enum
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
    source: Literal["pagerduty", "datadog", "manual"]
    title: str
    description: str
    severity: AlertSeverity
    service: str
    triggered_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)
