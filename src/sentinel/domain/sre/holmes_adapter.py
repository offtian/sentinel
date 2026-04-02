from __future__ import annotations

import abc
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import attrs

from sentinel.domain.resilience.circuit_breaker import CircuitBreaker
from sentinel.domain.sre import entities, investigation
from sentinel.domain.vendor_adapters.observability import BaseObservabilityClient
from sentinel.utils import logs


logger = logs.get_logger()


@attrs.frozen
class HolmesInvestigationResult:
    """Raw result from HolmesGPT investigation engine."""

    analysis: str
    tool_calls: list[dict[str, Any]]
    sources_queried: list[str]


class BaseHolmesAdapter(investigation.BaseInvestigationAdapter):
    """
    Abstract adapter for HolmesGPT investigation engine.

    Extends BaseInvestigationAdapter for backward compatibility.
    Subclasses must implement both ``investigate()`` and ``is_configured``.
    """

    @abc.abstractmethod
    async def investigate(  # type: ignore[override]
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> HolmesInvestigationResult:
        """
        Run a HolmesGPT investigation for the given alert.

        :param alert: The alert to investigate.
        :param context: Optional investigation context (ignored by Holmes adapters).
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

    @property
    def is_configured(self) -> bool:
        return self._enabled

    async def investigate(  # type: ignore[override]
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
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


class DirectToolsetAdapter(BaseHolmesAdapter):
    """
    Gather observability data directly from vendor adapters instead of HolmesGPT SDK.

    Queries Datadog logs, metrics, and traces concurrently for the alerted service,
    then returns structured findings for the root cause analyser agent.
    """

    def __init__(
        self,
        *,
        observability_client: BaseObservabilityClient | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        time_range_minutes: int = 60,
    ) -> None:
        self._obs_client = observability_client
        self._circuit_breaker = circuit_breaker
        self._time_range_minutes = time_range_minutes

    @property
    def is_configured(self) -> bool:
        return self._obs_client is not None and self._obs_client.is_configured

    async def investigate(  # type: ignore[override]
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> HolmesInvestigationResult:
        logs.log_event(
            "direct_investigation_started",
            params={
                "alert_id": alert.id,
                "alert_source": alert.source,
                "service": alert.service,
            },
        )

        tool_calls: list[dict[str, Any]] = []
        sources_queried: list[str] = []

        if self._obs_client is None or not self._obs_client.is_configured:
            return HolmesInvestigationResult(
                analysis=(
                    f"No observability backends configured for alert: {alert.title}. "
                    "Manual investigation required."
                ),
                tool_calls=[],
                sources_queried=[],
            )

        log_query = self._obs_client.log_query_template(service=alert.service)
        metrics_query = self._obs_client.metrics_query_template(service=alert.service)
        trace_query = self._obs_client.trace_query_template(service=alert.service)

        log_results, metric_results, trace_results = await asyncio.gather(
            self._safe_query(
                self._obs_client.query_logs,
                query=log_query,
                time_range_minutes=self._time_range_minutes,
                limit=50,
            ),
            self._safe_query(
                self._obs_client.query_metrics,
                query=metrics_query,
                time_range_minutes=self._time_range_minutes,
            ),
            self._safe_query(
                self._obs_client.query_traces,
                query=trace_query,
                time_range_minutes=self._time_range_minutes,
                limit=20,
            ),
        )

        # Summarise each result set once; reuse in both tool_calls and analysis.
        log_summary = _summarise_logs(log_results) if log_results is not None else None
        metrics_summary = (
            _summarise_metrics(metric_results) if metric_results is not None else None
        )
        traces_summary = _summarise_traces(trace_results) if trace_results is not None else None

        if log_results is not None:
            sources_queried.append("datadog_logs")
            tool_calls.append(
                {
                    "tool": "datadog_query_logs",
                    "query": log_query,
                    "result_count": len(log_results),
                    "result": log_summary,
                }
            )

        if metric_results is not None:
            sources_queried.append("datadog_metrics")
            tool_calls.append(
                {
                    "tool": "datadog_query_metrics",
                    "query": metrics_query,
                    "result_count": len(metric_results),
                    "result": metrics_summary,
                }
            )

        if trace_results is not None:
            sources_queried.append("datadog_traces")
            tool_calls.append(
                {
                    "tool": "datadog_query_traces",
                    "query": trace_query,
                    "result_count": len(trace_results),
                    "result": traces_summary,
                }
            )

        analysis = _build_analysis(
            alert=alert,
            log_summary=log_summary,
            metrics_summary=metrics_summary,
            traces_summary=traces_summary,
            log_count=len(log_results) if log_results is not None else None,
            metrics_count=len(metric_results) if metric_results is not None else None,
            traces_count=len(trace_results) if trace_results is not None else None,
        )

        logs.log_event(
            "direct_investigation_completed",
            params={
                "alert_id": alert.id,
                "sources_queried": sources_queried,
                "tool_call_count": len(tool_calls),
            },
        )

        return HolmesInvestigationResult(
            analysis=analysis,
            tool_calls=tool_calls,
            sources_queried=sources_queried,
        )

    async def _safe_query(
        self,
        fn: Callable[..., Awaitable[list[dict[str, Any]]]],
        **kwargs: Any,
    ) -> list[dict[str, Any]] | None:
        """
        Execute a query, optionally through the circuit breaker.

        Return None on failure so partial results from other queries are preserved.
        """
        try:
            if self._circuit_breaker:
                return await self._circuit_breaker.call(fn, **kwargs)
            return await fn(**kwargs)
        except Exception as e:
            logs.log_exception(e, params={"query_fn": fn.__name__})
            return None


def _summarise_logs(results: list[dict[str, Any]], *, limit: int = 10) -> str:
    """Build a human-readable summary of recent log entries."""
    if not results:
        return "No error logs found in the time window."
    lines = [f"Found {len(results)} error log(s). Top entries:"]
    for entry in results[:limit]:
        ts = entry.get("timestamp", "")
        msg = entry.get("message", "")[:200]
        svc = entry.get("service", "")
        lines.append(f"  [{ts}] ({svc}) {msg}")
    return "\n".join(lines)


def _summarise_metrics(results: list[dict[str, Any]]) -> str:
    """Build a human-readable summary of metric series."""
    if not results:
        return "No metric data found in the time window."
    lines = [f"Found {len(results)} metric series:"]
    for series in results:
        metric = series.get("metric", "unknown")
        scope = series.get("scope", "")
        points = series.get("points", [])
        lines.append(f"  {metric} ({scope}) - {len(points)} data points")
    return "\n".join(lines)


def _summarise_traces(results: list[dict[str, Any]], *, limit: int = 10) -> str:
    """Build a human-readable summary of trace spans."""
    if not results:
        return "No error traces found in the time window."
    lines = [f"Found {len(results)} error span(s). Top entries:"]
    for span in results[:limit]:
        svc = span.get("service", "")
        resource = span.get("resource", "")
        duration_ms = span.get("duration_ns", 0) / 1_000_000
        lines.append(f"  ({svc}) {resource} - {duration_ms:.0f}ms")
    return "\n".join(lines)


def _build_analysis(
    *,
    alert: entities.Alert,
    log_summary: str | None,
    metrics_summary: str | None,
    traces_summary: str | None,
    log_count: int | None,
    metrics_count: int | None,
    traces_count: int | None,
) -> str:
    """Combine pre-computed summaries into a structured analysis for the root cause agent."""
    sections: list[str] = [
        f"Investigation for alert: {alert.title}",
        f"Service: {alert.service} | Severity: {alert.severity.value}",
        f"Description: {alert.description}",
        "",
    ]

    if log_summary is not None:
        sections.append(f"## Logs ({log_count} entries)")
        sections.append(log_summary)
        sections.append("")

    if metrics_summary is not None:
        sections.append(f"## Metrics ({metrics_count} series)")
        sections.append(metrics_summary)
        sections.append("")

    if traces_summary is not None:
        sections.append(f"## Traces ({traces_count} spans)")
        sections.append(traces_summary)
        sections.append("")

    queried = sum(1 for s in (log_summary, metrics_summary, traces_summary) if s is not None)
    if queried == 0:
        sections.append("No data sources returned results. Manual investigation needed.")

    return "\n".join(sections)
