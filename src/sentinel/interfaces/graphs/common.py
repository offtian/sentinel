from __future__ import annotations

import abc
from typing import Any

from pydantic import BaseModel

from sentinel.domain.confidence import entities as confidence_entities


class InvestigationReply(BaseModel):
    """Output from the SRE investigation pipeline."""

    alert_id: str
    root_cause: str | None = None
    remediation: str | None = None
    confidence: confidence_entities.ConfidenceScore | None = None
    findings_summary: str = ""
    sources_queried: list[str] | None = None


class SupportReply(BaseModel):
    """Output from the support review pipeline."""

    ticket_id: str
    ticket_key: str
    suggested_response: str
    sources: list[dict[str, Any]] | None = None
    confidence: confidence_entities.ConfidenceScore | None = None
    category: str | None = None


class StatusUpdateClient(abc.ABC):
    @abc.abstractmethod
    async def update_status(self, message: str) -> None:
        """Update the current processing status for user feedback."""


class NoOpStatusUpdateClient(StatusUpdateClient):
    async def update_status(self, message: str) -> None:
        pass
