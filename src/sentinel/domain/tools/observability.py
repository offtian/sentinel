"""
Read-only observability tools for SRE investigation agents.

Each function queries an observability backend (Datadog, Grafana, etc.)
and returns a human-readable summary string.  Functions are intentionally
framework-agnostic — they accept typed parameters and return ``str``,
making them testable without PydanticAI.

Callers are responsible for supplying a configured
``BaseObservabilityClient``.  When the client is ``None`` or not
configured, every function returns a descriptive fallback message
instead of raising.
"""

from __future__ import annotations

from sentinel.domain.vendor_adapters.observability import base as obs_base
from sentinel.utils import logs


async def query_recent_logs(
    *,
    client: obs_base.BaseObservabilityClient | None,
    service: str,
    query: str,
    minutes_back: int = 30,
) -> str:
    """
    Search error and warning logs for a service within a time window.

    Return a formatted summary of matching log entries, or a fallback
    message when the client is unavailable.
    """
    if client is None or not client.is_configured:
        return "Observability client not available. Unable to query logs."

    try:
        full_query = f"service:{service} {query}"
        results = await client.query_logs(
            query=full_query, time_range_minutes=minutes_back, limit=50
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "query_recent_logs", "service": service})
        return f"Log query failed: {type(exc).__name__} — {exc}"

    if not results:
        return (
            f"No matching logs found for service '{service}' in the last {minutes_back} minutes."
        )

    lines = [f"Found {len(results)} log entries for '{service}':"]
    for entry in results[:10]:
        ts = entry.get("timestamp", "")
        msg = str(entry.get("message", ""))[:200]
        status = entry.get("status", "")
        lines.append(f"  [{ts}] ({status}) {msg}")

    if len(results) > 10:
        lines.append(f"  ... and {len(results) - 10} more entries")

    return "\n".join(lines)


async def query_metrics(
    *,
    client: obs_base.BaseObservabilityClient | None,
    service: str,
    metric_name: str,
    minutes_back: int = 60,
) -> str:
    """
    Fetch metric time series for a service.

    Return a formatted summary of metric data points, or a fallback
    message when the client is unavailable.
    """
    if client is None or not client.is_configured:
        return "Observability client not available. Unable to query metrics."

    try:
        full_query = f"{metric_name}{{service={service}}}"
        results = await client.query_metrics(query=full_query, time_range_minutes=minutes_back)
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "query_metrics", "service": service})
        return f"Metrics query failed: {type(exc).__name__} — {exc}"

    if not results:
        return f"No metric data found for '{metric_name}' on service '{service}'."

    lines = [f"Found {len(results)} metric series for '{metric_name}' on '{service}':"]
    for series in results:
        metric = series.get("metric", "unknown")
        scope = series.get("scope", "")
        points = series.get("points", [])
        if points:
            values = [p[1] for p in points if len(p) > 1]
            if values:
                lines.append(
                    f"  {metric} ({scope}): {len(points)} points, "
                    f"min={min(values):.2f}, max={max(values):.2f}, latest={values[-1]:.2f}"
                )
            else:
                lines.append(f"  {metric} ({scope}): {len(points)} points")
        else:
            lines.append(f"  {metric} ({scope}): no data points")

    return "\n".join(lines)


async def query_error_traces(
    *,
    client: obs_base.BaseObservabilityClient | None,
    service: str,
    minutes_back: int = 30,
) -> str:
    """
    Search distributed traces for error spans in a service.

    Return a formatted summary of error traces, or a fallback message
    when the client is unavailable.
    """
    if client is None or not client.is_configured:
        return "Observability client not available. Unable to query traces."

    try:
        full_query = f"service:{service} status:error"
        results = await client.query_traces(
            query=full_query, time_range_minutes=minutes_back, limit=20
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "query_error_traces", "service": service})
        return f"Trace query failed: {type(exc).__name__} — {exc}"

    if not results:
        return f"No error traces found for service '{service}' in the last {minutes_back} minutes."

    lines = [f"Found {len(results)} error traces for '{service}':"]
    for span in results[:10]:
        trace_id = span.get("trace_id", "?")[:12]
        resource = span.get("resource", "unknown")
        duration_ns = span.get("duration_ns", 0)
        duration_ms = duration_ns / 1_000_000 if duration_ns else 0
        status = span.get("status", "error")
        lines.append(f"  [{trace_id}] {resource} — {duration_ms:.0f}ms ({status})")

    if len(results) > 10:
        lines.append(f"  ... and {len(results) - 10} more traces")

    return "\n".join(lines)
