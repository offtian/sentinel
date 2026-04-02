"""
Native Kubernetes investigation adapter.

Uses a PydanticAI agent with the ``kubernetes`` Python client tools
to query cluster state and diagnose production incidents.

The actual agent execution is injected via ``agent_runner`` to respect
layer boundaries — the domain layer cannot import from interfaces or
plugins.  The default runner is wired in ``config.py``.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import attrs

from sentinel.domain.sre import entities, investigation
from sentinel.domain.tools import kubernetes as k8s_tools
from sentinel.utils import logs


logger = logs.get_logger()

AgentRunner = Callable[..., Awaitable["AgentResult"]]


@attrs.frozen
class AgentResult:
    """Result from the K8s investigator agent run."""

    root_cause: str
    confidence: float
    evidence: tuple[str, ...] | list[str]
    remediation_steps: tuple[str, ...] | list[str]
    affected_resources: tuple[str, ...] | list[str]
    timeline: str
    audit_entries: tuple[investigation.AuditEntry, ...] | list[investigation.AuditEntry]


class NativeK8sAgent(investigation.K8sInvestigationAdapter):
    """
    Investigate alerts using a PydanticAI agent with native K8s tools.

    Queries pod status, deployment status, events, and logs via the
    ``kubernetes`` Python async client.
    """

    def __init__(
        self,
        *,
        k8s_client: k8s_tools.K8sClient | None = None,
        model_name: str = "openai:gpt-4.1",
        mcp_toolsets: Sequence[Any] = (),
        agent_runner: AgentRunner | None = None,
    ) -> None:
        self._k8s_client = k8s_client
        self._model_name = model_name
        self._mcp_toolsets = mcp_toolsets
        self._agent_runner = agent_runner

    @property
    def is_configured(self) -> bool:
        return self._k8s_client is not None and self._k8s_client.is_configured

    async def investigate(
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> investigation.InvestigationResult:
        start_time = time.monotonic()
        started_at = datetime.now(tz=UTC)

        logs.log_event(
            "k8s_native_investigation_started",
            params={
                "alert_id": alert.id,
                "service": alert.service,
                "cluster": context.cluster_name if context else "unknown",
                "namespace": context.namespace if context else None,
            },
        )

        if not self.is_configured:
            return investigation.InvestigationResult(
                findings=(),
                sources_queried=(),
                duration_ms=0,
                adapter_name="native_k8s",
                audit_trail=(
                    investigation.AuditEntry(
                        timestamp=started_at,
                        adapter_name="native_k8s",
                        action="configuration_check",
                        tool_name=None,
                        status="error",
                        duration_ms=0,
                        error_code=None,
                        payload={"reason": "K8s client not configured"},
                    ),
                ),
            )

        if self._agent_runner is None:
            msg = "agent_runner must be provided for configured NativeK8sAgent"
            raise RuntimeError(msg)

        agent_result = await self._agent_runner(
            alert=alert,
            context=context,
            k8s_client=self._k8s_client,
            model_name=self._model_name,
            mcp_toolsets=self._mcp_toolsets,
        )

        duration_ms = int((time.monotonic() - start_time) * 1000)

        findings = tuple(
            entities.Finding(
                source="kubernetes",
                summary=evidence_item,
                relevance=agent_result.confidence,
            )
            for evidence_item in agent_result.evidence
        )

        sources = ("kubernetes_pods", "kubernetes_events", "kubernetes_logs")

        logs.log_event(
            "k8s_native_investigation_completed",
            params={
                "alert_id": alert.id,
                "findings_count": len(findings),
                "confidence": agent_result.confidence,
                "duration_ms": duration_ms,
            },
        )

        return investigation.InvestigationResult(
            findings=findings,
            sources_queried=sources,
            duration_ms=duration_ms,
            adapter_name="native_k8s",
            audit_trail=tuple(agent_result.audit_entries),
        )
