"""
MCP server tools wrapping Sentinel's observability domain functions.
"""

from __future__ import annotations

from sentinel.domain.tools import observability as obs_tools
from sentinel.domain.vendor_adapters.observability import base as obs_base
from sentinel.interfaces.mcp.tools import _helpers


async def query_logs(
    *,
    obs_client: obs_base.BaseObservabilityClient | None,
    service: str,
    query: str = "error OR warn",
    minutes_back: int = 30,
) -> str:
    """
    Search recent logs for a service.
    """
    return await _helpers.instrumented_mcp_tool(
        tool_name="query_logs",
        params={"service": service, "query": query},
        fn=lambda: obs_tools.query_recent_logs(
            client=obs_client,
            service=service,
            query=query,
            minutes_back=minutes_back,
        ),
    )


async def query_metrics(
    *,
    obs_client: obs_base.BaseObservabilityClient | None,
    service: str,
    metric_name: str = "cpu",
    minutes_back: int = 60,
) -> str:
    """
    Fetch metric time series for a service.
    """
    return await _helpers.instrumented_mcp_tool(
        tool_name="query_metrics",
        params={"service": service, "metric_name": metric_name},
        fn=lambda: obs_tools.query_metrics(
            client=obs_client,
            service=service,
            metric_name=metric_name,
            minutes_back=minutes_back,
        ),
    )


async def query_error_traces(
    *,
    obs_client: obs_base.BaseObservabilityClient | None,
    service: str,
    minutes_back: int = 30,
) -> str:
    """
    Search distributed traces for error spans.
    """
    return await _helpers.instrumented_mcp_tool(
        tool_name="query_error_traces",
        params={"service": service},
        fn=lambda: obs_tools.query_error_traces(
            client=obs_client,
            service=service,
            minutes_back=minutes_back,
        ),
    )
