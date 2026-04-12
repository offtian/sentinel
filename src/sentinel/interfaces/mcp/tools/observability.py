"""
MCP server tools wrapping Sentinel's observability domain functions.
"""

from __future__ import annotations

import time

from sentinel.domain.tools import observability as obs_tools
from sentinel.domain.vendor_adapters.observability import base as obs_base
from sentinel.utils import logs


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
    logs.log_event(
        "mcp_tool_invoked",
        params={"tool": "query_logs", "service": service, "query": query},
    )
    start = time.monotonic()
    try:
        result = await obs_tools.query_recent_logs(
            client=obs_client,
            service=service,
            query=query,
            minutes_back=minutes_back,
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "query_logs", "service": service})
        raise
    duration_ms = (time.monotonic() - start) * 1000
    logs.log_event(
        "mcp_tool_completed",
        params={"tool": "query_logs", "duration_ms": duration_ms},
    )
    return result


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
    logs.log_event(
        "mcp_tool_invoked",
        params={"tool": "query_metrics", "service": service, "metric_name": metric_name},
    )
    start = time.monotonic()
    try:
        result = await obs_tools.query_metrics(
            client=obs_client,
            service=service,
            metric_name=metric_name,
            minutes_back=minutes_back,
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "query_metrics", "service": service})
        raise
    duration_ms = (time.monotonic() - start) * 1000
    logs.log_event(
        "mcp_tool_completed",
        params={"tool": "query_metrics", "duration_ms": duration_ms},
    )
    return result


async def query_error_traces(
    *,
    obs_client: obs_base.BaseObservabilityClient | None,
    service: str,
    minutes_back: int = 30,
) -> str:
    """
    Search distributed traces for error spans.
    """
    logs.log_event(
        "mcp_tool_invoked",
        params={"tool": "query_error_traces", "service": service},
    )
    start = time.monotonic()
    try:
        result = await obs_tools.query_error_traces(
            client=obs_client,
            service=service,
            minutes_back=minutes_back,
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "query_error_traces", "service": service})
        raise
    duration_ms = (time.monotonic() - start) * 1000
    logs.log_event(
        "mcp_tool_completed",
        params={"tool": "query_error_traces", "duration_ms": duration_ms},
    )
    return result
