"""
Investigation adapter hierarchy and shared result types.

Defines the contract that all investigation backends must implement,
plus the audit trail envelope for hedge fund compliance traceability.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import attrs

from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.investigations import entities as investigation_entities


@attrs.frozen
class AuditEntry:
    """
    Record of a single action taken during an investigation.

    Uses a typed envelope (stable queryable fields) with a freeform
    payload for tool-specific detail.  New tools write different keys
    into ``payload`` — no schema changes needed.
    """

    timestamp: datetime
    adapter_name: str
    action: str
    tool_name: str | None
    status: str
    duration_ms: int
    error_code: str | None
    payload: Mapping[str, Any]


@attrs.frozen
class InvestigationContext:
    """
    Context for a K8s-aware investigation.

    Passed to adapters so they know which cluster and namespace to query.
    """

    cluster_name: str
    namespace: str | None = None
    additional_sources: tuple[str, ...] = ()


@attrs.frozen
class InvestigationResult:
    """
    Unified result from any investigation adapter.

    Carries the audit trail so every action is traceable.
    """

    findings: tuple[investigation_entities.Finding, ...]
    sources_queried: tuple[str, ...]
    duration_ms: int
    adapter_name: str
    audit_trail: tuple[AuditEntry, ...] = ()


class BaseInvestigationAdapter(abc.ABC):
    """
    Abstract adapter for investigation backends.

    All investigation backends (Holmes, native K8s, kagent) implement
    this contract.  Results flow through the same confidence scoring
    and approval gate pipeline.
    """

    @abc.abstractmethod
    async def investigate(
        self,
        *,
        alert: alert_entities.Alert,
        context: InvestigationContext | None = None,
    ) -> InvestigationResult:
        """
        Run an investigation for the given alert.

        :param alert: The alert to investigate.
        :param context: Optional K8s context (cluster, namespace).
        :returns: Structured findings with audit trail.
        """

    @property
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """
        Return True when this adapter has the credentials/config it needs.
        """


class K8sInvestigationAdapter(BaseInvestigationAdapter):
    """
    Abstract adapter for Kubernetes-specific investigation backends.

    Adds K8s-specific context (cluster, namespace) to the base contract.
    Concrete implementations: NativeK8sAgent, KagentAdapter.
    """
