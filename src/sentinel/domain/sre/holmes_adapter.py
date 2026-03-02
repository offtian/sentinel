from __future__ import annotations

import abc
from typing import Any

import attrs

from sentinel.domain.sre import entities
from sentinel.utils import logs


logger = logs.get_logger()


@attrs.frozen
class HolmesInvestigationResult:
    """Raw result from HolmesGPT investigation engine."""

    analysis: str
    tool_calls: list[dict[str, Any]]
    sources_queried: list[str]


class BaseHolmesAdapter(abc.ABC):
    """
    Abstract adapter for HolmesGPT investigation engine.

    This allows swapping between the real HolmesGPT SDK and a mock for testing.
    """

    @abc.abstractmethod
    async def investigate(
        self,
        *,
        alert: entities.Alert,
    ) -> HolmesInvestigationResult:
        """
        Run a HolmesGPT investigation for the given alert.

        Uses HolmesGPT's toolsets (Datadog, Kubernetes, Prometheus) to gather
        context, then returns raw findings for our pipeline to analyse.
        """


class HolmesAdapter(BaseHolmesAdapter):
    """
    Production adapter that wraps the HolmesGPT SDK.

    Uses HolmesGPT's toolsets for data gathering but delegates analysis
    to our PydanticAI agents.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        enabled: bool = True,
    ) -> None:
        self._api_key = api_key
        self._enabled = enabled

    async def investigate(
        self,
        *,
        alert: entities.Alert,
    ) -> HolmesInvestigationResult:
        if not self._enabled:
            return HolmesInvestigationResult(
                analysis="HolmesGPT is disabled. No automated investigation performed.",
                tool_calls=[],
                sources_queried=[],
            )

        # TODO: Integrate with actual HolmesGPT SDK once dependency is resolved.
        # The integration will look like:
        #
        # from holmes.core.supabase_dal import SupabaseDal
        # from holmes.core.tool_calling_llm import ToolCallingLLM
        # from holmes.plugins.toolsets import DatadogToolset, KubernetesToolset
        #
        # toolsets = [DatadogToolset(), KubernetesToolset()]
        # llm = ToolCallingLLM(model="gpt-4.1", tools=toolsets)
        # result = await llm.investigate(alert_description=alert.description)

        logs.log_event(
            "holmes_investigation_started",
            params={
                "alert_id": alert.id,
                "alert_source": alert.source,
                "alert_title": alert.title,
            },
        )

        return HolmesInvestigationResult(
            analysis=f"Investigation pending for alert: {alert.title}",
            tool_calls=[],
            sources_queried=[],
        )


class MockHolmesAdapter(BaseHolmesAdapter):
    """Mock adapter for testing."""

    def __init__(self, *, result: HolmesInvestigationResult | None = None) -> None:
        self._result = result or HolmesInvestigationResult(
            analysis="Mock investigation: no issues found.",
            tool_calls=[
                {"tool": "datadog_query_logs", "result": "No errors in last 30 minutes"},
                {"tool": "kubernetes_get_pods", "result": "All pods healthy"},
            ],
            sources_queried=["datadog_logs", "kubernetes"],
        )

    async def investigate(
        self,
        *,
        alert: entities.Alert,
    ) -> HolmesInvestigationResult:
        return self._result
