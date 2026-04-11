from __future__ import annotations

import abc
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import attrs

from sentinel.domain.resilience.circuit_breaker import CircuitBreaker
from sentinel.domain.sre import entities, investigation
from sentinel.domain.tools import kubernetes as k8s_tools
from sentinel.domain.vendor_adapters.observability import BaseObservabilityClient
from sentinel.utils import logs


try:
    from holmes.core import llm as _holmes_llm_mod
    from holmes.core import tool_calling_llm as _holmes_tcllm_mod
    from holmes.core.tools_utils import tool_executor as _holmes_executor_mod

    _HOLMES_SDK_AVAILABLE = True
except ImportError:
    _HOLMES_SDK_AVAILABLE = False


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
    Production adapter that runs investigations via the HolmesGPT SDK.

    Uses the HolmesGPT ``ToolCallingLLM`` engine to autonomously query
    observability and infrastructure toolsets, then returns a structured
    analysis.  Falls back to ``DirectToolsetAdapter`` when the SDK is not
    installed or no toolsets are configured.
    """

    _SYSTEM_PROMPT = (
        "You are an expert SRE investigating a production alert. "
        "Use the available tools to query logs, metrics, traces, and "
        "infrastructure state. Analyse the data you gather and provide "
        "a concise root-cause analysis with recommended next steps."
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        enabled: bool = True,
        model: str = "openai/gpt-4.1",
        api_base: str | None = None,
        toolsets: tuple[Any, ...] = (),
        max_steps: int = 10,
    ) -> None:
        self._api_key = api_key
        self._enabled = enabled
        self._model = model
        self._api_base = api_base
        self._toolsets = toolsets
        self._max_steps = max_steps

    @property
    def is_configured(self) -> bool:
        return self._enabled and bool(self._toolsets) and _HOLMES_SDK_AVAILABLE

    async def investigate(  # type: ignore[override]
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> HolmesInvestigationResult:
        """
        Run a HolmesGPT investigation for the given alert.

        :param alert: The alert to investigate.
        :param context: Optional investigation context (unused by this adapter).
        :raises RuntimeError: If the HolmesGPT SDK is not installed.
        """
        if not self._enabled or not self._toolsets:
            return HolmesInvestigationResult(
                analysis="HolmesGPT is disabled. No automated investigation performed.",
                tool_calls=[],
                sources_queried=[],
            )

        if not _HOLMES_SDK_AVAILABLE:
            logs.log_event(
                "holmes_sdk_unavailable",
                params={"alert_id": alert.id},
            )
            return HolmesInvestigationResult(
                analysis="HolmesGPT SDK is not installed. No automated investigation performed.",
                tool_calls=[],
                sources_queried=[],
            )

        logs.log_event(
            "holmes_investigation_started",
            params={
                "alert_id": alert.id,
                "alert_source": alert.source,
                "alert_title": alert.title,
                "model": self._model,
                "toolset_count": len(self._toolsets),
                "max_steps": self._max_steps,
            },
        )

        try:
            result = await self._run_holmes(alert=alert)
        except Exception as exc:
            logs.log_exception(
                exc,
                params={
                    "alert_id": alert.id,
                    "alert_title": alert.title,
                },
            )
            return HolmesInvestigationResult(
                analysis=(
                    f"HolmesGPT investigation failed for alert: {alert.title}. Error: {exc}"
                ),
                tool_calls=[],
                sources_queried=[],
            )

        tool_call_dicts = _extract_tool_calls(result)
        sources = _extract_sources(result)

        logs.log_event(
            "holmes_investigation_completed",
            params={
                "alert_id": alert.id,
                "tool_call_count": len(tool_call_dicts),
                "sources_queried": sources,
            },
        )

        return HolmesInvestigationResult(
            analysis=result.result or f"No analysis produced for alert: {alert.title}",
            tool_calls=tool_call_dicts,
            sources_queried=sources,
        )

    async def _run_holmes(
        self,
        *,
        alert: entities.Alert,
    ) -> _holmes_tcllm_mod.LLMResult:
        """
        Build the HolmesGPT engine and execute a tool-calling investigation.

        The SDK's ``ToolCallingLLM.call()`` is synchronous, so it is
        dispatched to a thread pool via ``asyncio.to_thread``.
        """
        llm = _holmes_llm_mod.DefaultLLM(
            model=self._model,
            api_key=self._api_key,
            api_base=self._api_base,
        )
        executor = _holmes_executor_mod.ToolExecutor(
            toolsets=list(self._toolsets),
        )
        ai = _holmes_tcllm_mod.ToolCallingLLM(
            tool_executor=executor,
            max_steps=self._max_steps,
            llm=llm,
            tool_results_dir=None,
        )

        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Investigate the following production alert:\n\n"
                    f"Title: {alert.title}\n"
                    f"Service: {alert.service}\n"
                    f"Severity: {alert.severity.value}\n"
                    f"Description: {alert.description}\n"
                    f"Source: {alert.source}\n"
                    f"Triggered at: {alert.triggered_at}"
                ),
            },
        ]

        return await asyncio.to_thread(ai.call, messages)


class DirectToolsetAdapter(BaseHolmesAdapter):
    """
    Gather observability and Kubernetes data directly from vendor adapters.

    Queries Datadog logs, metrics, and traces concurrently for the alerted service,
    and optionally queries Kubernetes pod status, events, and pod logs when a
    K8s client and investigation context are provided.  Returns structured findings
    for the root cause analyser agent.
    """

    def __init__(
        self,
        *,
        observability_client: BaseObservabilityClient | None = None,
        k8s_client: k8s_tools.K8sClient | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        time_range_minutes: int = 60,
    ) -> None:
        self._obs_client = observability_client
        self._k8s_client = k8s_client
        self._circuit_breaker = circuit_breaker
        self._time_range_minutes = time_range_minutes

    @property
    def is_configured(self) -> bool:
        obs_ok = self._obs_client is not None and self._obs_client.is_configured
        k8s_ok = self._k8s_client is not None and self._k8s_client.is_configured
        return obs_ok or k8s_ok

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

        # -- Observability queries -------------------------------------------------
        obs_results = await self._query_observability(alert=alert)

        # -- Kubernetes queries ----------------------------------------------------
        k8s_results = await self._query_k8s(alert=alert, context=context)

        # -- Assemble observability results ----------------------------------------
        log_summary: str | None = None
        metrics_summary: str | None = None
        traces_summary: str | None = None

        if obs_results is not None:
            log_results, metric_results, trace_results = obs_results

            log_summary = _summarise_logs(log_results) if log_results is not None else None
            metrics_summary = (
                _summarise_metrics(metric_results) if metric_results is not None else None
            )
            traces_summary = (
                _summarise_traces(trace_results) if trace_results is not None else None
            )

            if log_results is not None:
                log_query = self._obs_client.log_query_template(service=alert.service)  # type: ignore[union-attr]
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
                metrics_query = self._obs_client.metrics_query_template(service=alert.service)  # type: ignore[union-attr]
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
                trace_query = self._obs_client.trace_query_template(service=alert.service)  # type: ignore[union-attr]
                sources_queried.append("datadog_traces")
                tool_calls.append(
                    {
                        "tool": "datadog_query_traces",
                        "query": trace_query,
                        "result_count": len(trace_results),
                        "result": traces_summary,
                    }
                )

        # -- Assemble K8s results --------------------------------------------------
        k8s_pod_summary: str | None = None
        k8s_events_summary: str | None = None
        k8s_logs_summary: str | None = None

        if k8s_results is not None:
            pod_status, k8s_events, pod_logs = k8s_results

            if pod_status is not None:
                k8s_pod_summary = _summarise_k8s_pod_status(pod_status)
                sources_queried.append("kubernetes_pod_status")
                tool_calls.append(
                    {
                        "tool": "k8s_pod_status",
                        "query": f"pod status for {alert.service}",
                        "result": k8s_pod_summary,
                    }
                )

            if k8s_events is not None:
                k8s_events_summary = _summarise_k8s_events(k8s_events)
                sources_queried.append("kubernetes_events")
                tool_calls.append(
                    {
                        "tool": "k8s_events",
                        "query": f"events for {alert.service}",
                        "result": k8s_events_summary,
                    }
                )

            if pod_logs is not None:
                k8s_logs_summary = pod_logs
                sources_queried.append("kubernetes_pod_logs")
                tool_calls.append(
                    {
                        "tool": "k8s_pod_logs",
                        "query": f"pod logs for {alert.service}",
                        "result": k8s_logs_summary,
                    }
                )

        # -- No backends at all? ---------------------------------------------------
        if obs_results is None and k8s_results is None:
            return HolmesInvestigationResult(
                analysis=(
                    f"No observability backends configured for alert: {alert.title}. "
                    "Manual investigation required."
                ),
                tool_calls=[],
                sources_queried=[],
            )

        # -- Build analysis --------------------------------------------------------
        analysis = _build_analysis(
            alert=alert,
            log_summary=log_summary,
            metrics_summary=metrics_summary,
            traces_summary=traces_summary,
            log_count=len(obs_results[0]) if obs_results and obs_results[0] is not None else None,
            metrics_count=len(obs_results[1])
            if obs_results and obs_results[1] is not None
            else None,
            traces_count=len(obs_results[2])
            if obs_results and obs_results[2] is not None
            else None,
            k8s_pod_summary=k8s_pod_summary,
            k8s_events_summary=k8s_events_summary,
            k8s_logs_summary=k8s_logs_summary,
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

    async def _query_observability(
        self,
        *,
        alert: entities.Alert,
    ) -> (
        tuple[
            list[dict[str, Any]] | None,
            list[dict[str, Any]] | None,
            list[dict[str, Any]] | None,
        ]
        | None
    ):
        """
        Query observability backend concurrently for logs, metrics, and traces.

        Return None when the observability client is unavailable or unconfigured.
        """
        if self._obs_client is None or not self._obs_client.is_configured:
            return None

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

        return (log_results, metric_results, trace_results)

    async def _query_k8s(
        self,
        *,
        alert: entities.Alert,
        context: investigation.InvestigationContext | None,
    ) -> (
        tuple[
            dict[str, Any] | None,
            list[dict[str, Any]] | None,
            str | None,
        ]
        | None
    ):
        """
        Query Kubernetes for pod status, events, and pod logs.

        Return None when K8s queries should be skipped (no context, no client,
        or client not configured).
        """
        if context is None:
            return None
        if self._k8s_client is None or not self._k8s_client.is_configured:
            return None

        namespace = context.namespace or "default"

        pod_status, k8s_events, pod_logs = await asyncio.gather(
            self._safe_k8s_query(
                self._k8s_client.get_pod_status,
                namespace=namespace,
                pod_name=alert.service,
            ),
            self._safe_k8s_query(
                self._k8s_client.get_recent_events,
                namespace=namespace,
                resource_name=alert.service,
            ),
            self._safe_k8s_query(
                self._k8s_client.get_pod_logs,
                namespace=namespace,
                pod_name=alert.service,
            ),
        )

        return (pod_status, k8s_events, pod_logs)

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

    async def _safe_k8s_query(
        self,
        fn: Callable[..., Awaitable[Any]],
        **kwargs: Any,
    ) -> Any | None:
        """
        Execute a K8s query, optionally through the circuit breaker.

        Return None on failure so partial results from other queries are preserved.
        """
        try:
            if self._circuit_breaker:
                return await self._circuit_breaker.call(fn, **kwargs)
            return await fn(**kwargs)
        except Exception as e:
            logs.log_exception(e, params={"query_fn": fn.__name__})
            return None


def _extract_tool_calls(result: _holmes_tcllm_mod.LLMResult) -> list[dict[str, Any]]:
    """Map HolmesGPT ``ToolCallResult`` objects to plain dicts."""
    if not result.tool_calls:
        return []
    return [
        {
            "tool": tc.tool_name,
            "query": tc.description,
            "result": tc.result.get_stringified_data(),
            "status": tc.result.status.value,
        }
        for tc in result.tool_calls
    ]


def _extract_sources(result: _holmes_tcllm_mod.LLMResult) -> list[str]:
    """Return unique toolset names from the HolmesGPT result."""
    if not result.tool_calls:
        return []
    seen: set[str] = set()
    sources: list[str] = []
    for tc in result.tool_calls:
        name = tc.toolset_name or tc.tool_name
        if name not in seen:
            seen.add(name)
            sources.append(name)
    return sources


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


def _summarise_k8s_pod_status(data: dict[str, Any]) -> str:
    """Build a human-readable summary of Kubernetes pod status."""
    if not data:
        return "No pod status data available."

    name = data.get("name", "unknown")
    phase = data.get("phase", "Unknown")
    restart_count = data.get("restart_count", 0)
    conditions = data.get("conditions", [])

    lines = [
        f"Pod: {name}",
        f"Phase: {phase}",
        f"Restart count: {restart_count}",
    ]

    if conditions:
        lines.append("Conditions:")
        for cond in conditions:
            cond_type = cond.get("type", "?")
            status = cond.get("status", "?")
            reason = cond.get("reason", "")
            suffix = f" ({reason})" if reason else ""
            lines.append(f"  {cond_type}: {status}{suffix}")

    return "\n".join(lines)


def _summarise_k8s_events(events: list[dict[str, Any]]) -> str:
    """Build a human-readable summary of Kubernetes events."""
    if not events:
        return "No recent events found."

    lines = [f"Found {len(events)} event(s):"]
    for event in events:
        event_type = event.get("type", "?")
        reason = event.get("reason", "?")
        message = str(event.get("message", ""))[:200]
        ts = event.get("last_timestamp", "")
        count = event.get("count", 1)
        count_suffix = f" (x{count})" if count > 1 else ""
        lines.append(f"  [{ts}] {event_type}/{reason}: {message}{count_suffix}")

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
    k8s_pod_summary: str | None = None,
    k8s_events_summary: str | None = None,
    k8s_logs_summary: str | None = None,
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

    if (
        k8s_pod_summary is not None
        or k8s_events_summary is not None
        or k8s_logs_summary is not None
    ):
        sections.append("## Kubernetes")
        if k8s_pod_summary is not None:
            sections.append("### Pod Status")
            sections.append(k8s_pod_summary)
            sections.append("")
        if k8s_events_summary is not None:
            sections.append("### Events")
            sections.append(k8s_events_summary)
            sections.append("")
        if k8s_logs_summary is not None:
            sections.append("### Pod Logs")
            sections.append(k8s_logs_summary)
            sections.append("")

    queried = sum(
        1
        for s in (
            log_summary,
            metrics_summary,
            traces_summary,
            k8s_pod_summary,
            k8s_events_summary,
            k8s_logs_summary,
        )
        if s is not None
    )
    if queried == 0:
        sections.append("No data sources returned results. Manual investigation needed.")

    return "\n".join(sections)
