"""
Shared pipeline reply types and callback signatures.

These live in the domain layer so that both ``domain`` and ``application``
can depend on them without importing ``interfaces``.
"""

from __future__ import annotations

import abc
import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage

from sentinel.domain.confidence import entities as confidence_entities


# Callable types for optional persistence hooks injected into pipeline dependencies.
PersistInvestigationFn = Callable[["InvestigationReply"], Awaitable[None]]
PersistTicketReviewFn = Callable[["SupportReply"], Awaitable[None]]

# Callback for requesting human approval before publishing.
# Args: investigation_id, alert_id, alert_title, root_cause, remediation, confidence_label, findings_summary
# Returns: Slack message timestamp (str) or None if skipped.
RequestApprovalFn = Callable[
    [str, str, str, str | None, str | None, str | None, str],
    Awaitable[str | None],
]


@dataclasses.dataclass
class AgentTrace:
    """Captured message history from a single agent run."""

    agent_name: str
    messages: list[ModelMessage]


class TraceCollector:
    """
    Accumulate agent traces across graph nodes.

    Graph nodes append traces after each ``agent.run()`` call.
    The chat UI reads ``traces`` after the graph completes.
    """

    def __init__(self) -> None:
        self.traces: list[AgentTrace] = []

    def record(self, *, agent_name: str, messages: list[ModelMessage]) -> None:
        """Record a single agent invocation's message history."""
        self.traces.append(AgentTrace(agent_name=agent_name, messages=messages))


class InvestigationReply(BaseModel):
    """Output from the SRE investigation pipeline."""

    alert_id: str
    root_cause: str | None = None
    remediation: str | None = None
    confidence: confidence_entities.ConfidenceScore | None = None
    findings_summary: str = ""
    sources_queried: list[str] | None = None
    approval_status: str | None = (
        None  # "pending", "approved", "rejected", or None (no approval needed)
    )


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


class ChartStepTiming(BaseModel):
    """Timing for a single pipeline step."""

    step: str
    duration_ms: int = 0


class ChartGenerationReply(BaseModel):
    """Output from the chart generation pipeline."""

    service_name: str
    files_generated: int = 0
    validation_passed: bool = False
    policy_violations: int = 0
    generation_attempts: int = 1
    confidence: confidence_entities.ConfidenceScore | None = None
    pr_url: str = ""
    error: str | None = None
    total_duration_ms: int = 0
    step_timings: tuple[ChartStepTiming, ...] = ()
    parser_model: str = ""
    generator_model: str = ""


class NoOpStatusUpdateClient(StatusUpdateClient):
    async def update_status(self, message: str) -> None:
        pass
