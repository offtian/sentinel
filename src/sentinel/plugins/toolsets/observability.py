"""
Observability toolset for SRE investigation agents.

Wraps the domain tool functions from ``sentinel.domain.tools.observability``
into a PydanticAI ``FunctionToolset`` that can be injected at
``agent.run(toolsets=[...])`` time.

All tools are read-only and no-op when the observability client is
unavailable.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from sentinel.domain.tools import observability as obs_tools
from sentinel.domain.vendor_adapters.observability import base as obs_base


def build_observability_toolset(
    *,
    observability_client: obs_base.BaseObservabilityClient | None,
    service_name: str = "",
) -> FunctionToolset[Any]:
    """
    Build a read-only toolset for querying logs, metrics, and traces.

    The ``service_name`` default is baked into each tool closure so the
    LLM receives a sensible default when investigating a specific service,
    but can override it per-call.

    :param observability_client: The configured observability backend, or None.
    :param service_name: Default service name for queries (from the alert).
    """
    toolset: FunctionToolset[Any] = FunctionToolset()

    @toolset.tool
    async def query_recent_logs(
        ctx: RunContext[Any],
        service: str = service_name,
        query: str = "error OR warn",
        minutes_back: int = 30,
    ) -> str:
        """
        Search error and warning logs for a service within a time window.

        Use this to find log messages that may explain the root cause of an
        alert.  Start with a broad query, then narrow down.

        Args:
            ctx: PydanticAI run context (injected automatically).
            service: Service name to query logs for.  Defaults to the alerted service.
            query: Log search query (e.g. "timeout", "OOM", "connection refused").
            minutes_back: How far back to search, in minutes.
        """
        return await obs_tools.query_recent_logs(
            client=observability_client,
            service=service,
            query=query,
            minutes_back=minutes_back,
        )

    @toolset.tool
    async def query_metrics(
        ctx: RunContext[Any],
        service: str = service_name,
        metric_name: str = "cpu",
        minutes_back: int = 60,
    ) -> str:
        """
        Fetch metric time series for a service.

        Use this to check whether CPU, memory, latency, error rate, or other
        metrics show anomalies that correlate with the alert.

        Args:
            ctx: PydanticAI run context (injected automatically).
            service: Service name to query metrics for.
            metric_name: Metric to query (e.g. "cpu", "memory", "p99_latency", "error_rate").
            minutes_back: How far back to query, in minutes.
        """
        return await obs_tools.query_metrics(
            client=observability_client,
            service=service,
            metric_name=metric_name,
            minutes_back=minutes_back,
        )

    @toolset.tool
    async def query_error_traces(
        ctx: RunContext[Any],
        service: str = service_name,
        minutes_back: int = 30,
    ) -> str:
        """
        Search distributed traces for error spans in a service.

        Use this to identify which operations are failing, their duration,
        and upstream/downstream dependencies involved in the error path.

        Args:
            ctx: PydanticAI run context (injected automatically).
            service: Service name to query traces for.
            minutes_back: How far back to search, in minutes.
        """
        return await obs_tools.query_error_traces(
            client=observability_client,
            service=service,
            minutes_back=minutes_back,
        )

    return toolset
