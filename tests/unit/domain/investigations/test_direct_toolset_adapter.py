from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest

from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.investigations import adapters, holmes_adapter
from sentinel.domain.resilience.circuit_breaker import CircuitBreaker


# F7 (2026-04-27): DirectToolsetAdapter is archived alongside HolmesAdapter.
# Skipping wholesale; see test_holmes_adapter.py for full rationale.
pytestmark = pytest.mark.skip(reason="HolmesGPT integration archived in F7")


@pytest.fixture
def sample_alert():
    return alert_entities.Alert(
        id="P123ABC",
        source="pagerduty",
        title="High CPU usage on web-01",
        description="CPU usage exceeded 90% for 5 minutes",
        severity=alert_entities.AlertSeverity.HIGH,
        service="api-service",
        triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _make_datadog_client(
    *,
    is_configured: bool = True,
    logs: list[dict[str, Any]] | None = None,
    metrics: list[dict[str, Any]] | None = None,
    traces: list[dict[str, Any]] | None = None,
) -> mock.AsyncMock:
    client = mock.AsyncMock()
    client.is_configured = is_configured
    client.query_logs.return_value = logs or []
    client.query_metrics.return_value = metrics or []
    client.query_traces.return_value = traces or []
    # Query template methods are sync — use regular Mock to avoid coroutine return.
    client.log_query_template = mock.Mock(
        side_effect=lambda *, service: f"service:{service} error"
    )
    client.metrics_query_template = mock.Mock(
        side_effect=lambda *, service: f"cpu{{service={service}}}"
    )
    client.trace_query_template = mock.Mock(
        side_effect=lambda *, service: f"service:{service} status:error"
    )
    return client


class TestDirectToolsetAdapter:
    async def test_returns_empty_when_datadog_not_configured(self, sample_alert):
        # Given a Datadog client that is not configured
        client = _make_datadog_client(is_configured=False)
        adapter = holmes_adapter.DirectToolsetAdapter(observability_client=client)

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert)

        # Then the analysis indicates no backends configured
        assert "No observability backends configured" in result.analysis
        assert result.tool_calls == []
        assert result.sources_queried == []

    async def test_returns_empty_when_no_client(self, sample_alert):
        # Given no Datadog client at all
        adapter = holmes_adapter.DirectToolsetAdapter(observability_client=None)

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert)

        # Then the analysis indicates no backends configured
        assert "No observability backends configured" in result.analysis

    async def test_queries_all_sources_concurrently(self, sample_alert):
        # Given a configured Datadog client returning data
        log_entry = {
            "timestamp": "2024-01-01T00:00:00Z",
            "message": "OOMKilled",
            "service": "api-service",
            "status": "error",
        }
        metric_entry = {
            "metric": "system.cpu.user",
            "scope": "service:api-service",
            "points": [[1, 95.0]],
        }
        trace_entry = {
            "service": "api-service",
            "resource": "/api/health",
            "status": "error",
            "duration_ns": 5_000_000,
        }

        client = _make_datadog_client(
            logs=[log_entry],
            metrics=[metric_entry],
            traces=[trace_entry],
        )
        adapter = holmes_adapter.DirectToolsetAdapter(observability_client=client)

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert)

        # Then all three sources are queried
        assert "datadog_logs" in result.sources_queried
        assert "datadog_metrics" in result.sources_queried
        assert "datadog_traces" in result.sources_queried
        assert len(result.tool_calls) == 3

    async def test_analysis_contains_alert_context(self, sample_alert):
        # Given a configured Datadog client
        client = _make_datadog_client()
        adapter = holmes_adapter.DirectToolsetAdapter(observability_client=client)

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert)

        # Then the analysis contains the alert title and service
        assert sample_alert.title in result.analysis
        assert sample_alert.service in result.analysis

    async def test_partial_failure_preserves_other_results(self, sample_alert):
        # Given a Datadog client where logs fail but metrics/traces succeed
        client = _make_datadog_client(
            metrics=[{"metric": "cpu", "scope": "*", "points": []}],
            traces=[{"service": "api", "resource": "/", "status": "ok", "duration_ns": 1000}],
        )
        client.query_logs.side_effect = Exception("Datadog API timeout")
        adapter = holmes_adapter.DirectToolsetAdapter(observability_client=client)

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert)

        # Then metrics and traces are still present
        assert "datadog_metrics" in result.sources_queried
        assert "datadog_traces" in result.sources_queried
        assert "datadog_logs" not in result.sources_queried

    async def test_uses_circuit_breaker_when_provided(self, sample_alert):
        # Given a Datadog client and a circuit breaker
        client = _make_datadog_client()
        breaker = CircuitBreaker(name="datadog-test", failure_threshold=2)
        adapter = holmes_adapter.DirectToolsetAdapter(
            observability_client=client,
            circuit_breaker=breaker,
        )

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert)

        # Then results are returned (circuit is closed)
        assert len(result.sources_queried) == 3

    async def test_handles_circuit_open(self, sample_alert):
        # Given a Datadog client and a circuit breaker that is open
        client = _make_datadog_client()
        breaker = CircuitBreaker(name="datadog-test", failure_threshold=1)
        breaker.record_failure()  # Open the circuit
        adapter = holmes_adapter.DirectToolsetAdapter(
            observability_client=client,
            circuit_breaker=breaker,
        )

        # When an investigation is run with an open circuit
        result = await adapter.investigate(alert=sample_alert)

        # Then all queries fail gracefully (circuit blocks them)
        assert result.sources_queried == []
        assert result.tool_calls == []

    async def test_tool_calls_include_query_and_count(self, sample_alert):
        # Given a Datadog client returning specific results
        client = _make_datadog_client(
            logs=[
                {"timestamp": "t1", "message": "err1", "service": "svc", "status": "error"},
                {"timestamp": "t2", "message": "err2", "service": "svc", "status": "error"},
            ],
        )
        adapter = holmes_adapter.DirectToolsetAdapter(observability_client=client)

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert)

        # Then tool calls include the query and result count
        log_call = next(tc for tc in result.tool_calls if tc["tool"] == "datadog_query_logs")
        assert log_call["result_count"] == 2
        assert "api-service" in log_call["query"]


def _make_k8s_client(
    *,
    is_configured: bool = True,
    pod_status: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    pod_logs: str = "",
) -> mock.AsyncMock:
    client = mock.AsyncMock()
    client.is_configured = is_configured
    client.get_pod_status.return_value = pod_status or {
        "name": "api-service-abc123",
        "phase": "Running",
        "restart_count": 3,
        "conditions": [{"type": "Ready", "status": "True"}],
    }
    client.get_recent_events.return_value = events or [
        {
            "type": "Warning",
            "reason": "OOMKilled",
            "message": "Container exceeded memory limit",
            "last_timestamp": "2024-01-01T00:00:00Z",
            "count": 2,
        },
    ]
    client.get_pod_logs.return_value = (
        pod_logs or "ERROR 2024-01-01 OOMKilled\nERROR 2024-01-01 restart"
    )
    return client


class TestDirectToolsetAdapterK8s:
    async def test_queries_k8s_when_context_provided(self, sample_alert):
        # Given a configured observability client and K8s client with investigation context
        obs_client = _make_datadog_client()
        k8s_client = _make_k8s_client()
        context = adapters.InvestigationContext(
            cluster_name="prod-us-east",
            namespace="default",
        )
        adapter = holmes_adapter.DirectToolsetAdapter(
            observability_client=obs_client,
            k8s_client=k8s_client,
        )

        # When an investigation is run with K8s context
        result = await adapter.investigate(alert=sample_alert, context=context)

        # Then K8s sources appear alongside observability sources
        assert "kubernetes_pod_status" in result.sources_queried
        assert "kubernetes_events" in result.sources_queried
        assert "kubernetes_pod_logs" in result.sources_queried
        assert "datadog_logs" in result.sources_queried

    async def test_skips_k8s_when_no_context(self, sample_alert):
        # Given a configured K8s client but no investigation context
        obs_client = _make_datadog_client()
        k8s_client = _make_k8s_client()
        adapter = holmes_adapter.DirectToolsetAdapter(
            observability_client=obs_client,
            k8s_client=k8s_client,
        )

        # When an investigation is run without context
        result = await adapter.investigate(alert=sample_alert)

        # Then only observability sources are queried, no K8s
        assert "kubernetes_pod_status" not in result.sources_queried
        assert "kubernetes_events" not in result.sources_queried

    async def test_skips_k8s_when_client_not_configured(self, sample_alert):
        # Given a K8s client that is not configured
        obs_client = _make_datadog_client()
        k8s_client = _make_k8s_client(is_configured=False)
        context = adapters.InvestigationContext(
            cluster_name="prod-us-east",
            namespace="default",
        )
        adapter = holmes_adapter.DirectToolsetAdapter(
            observability_client=obs_client,
            k8s_client=k8s_client,
        )

        # When an investigation is run with context but unconfigured K8s
        result = await adapter.investigate(alert=sample_alert, context=context)

        # Then K8s sources are not queried
        assert "kubernetes_pod_status" not in result.sources_queried
        assert "datadog_logs" in result.sources_queried

    async def test_k8s_analysis_included_in_output(self, sample_alert):
        # Given a configured K8s client returning pod status data
        obs_client = _make_datadog_client()
        k8s_client = _make_k8s_client(
            pod_status={
                "name": "api-service-abc123",
                "phase": "CrashLoopBackOff",
                "restart_count": 15,
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "ContainersNotReady"}
                ],
            },
        )
        context = adapters.InvestigationContext(
            cluster_name="prod-us-east",
            namespace="default",
        )
        adapter = holmes_adapter.DirectToolsetAdapter(
            observability_client=obs_client,
            k8s_client=k8s_client,
        )

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert, context=context)

        # Then the analysis includes K8s findings
        assert "Kubernetes" in result.analysis or "Pod Status" in result.analysis

    async def test_k8s_partial_failure_preserves_other_k8s_results(self, sample_alert):
        # Given a K8s client where pod status fails but events and logs succeed
        obs_client = _make_datadog_client()
        k8s_client = _make_k8s_client()
        k8s_client.get_pod_status.side_effect = Exception("K8s API timeout")
        context = adapters.InvestigationContext(
            cluster_name="prod-us-east",
            namespace="default",
        )
        adapter = holmes_adapter.DirectToolsetAdapter(
            observability_client=obs_client,
            k8s_client=k8s_client,
        )

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert, context=context)

        # Then events and logs are still present despite pod status failure
        assert "kubernetes_pod_status" not in result.sources_queried
        assert "kubernetes_events" in result.sources_queried
        assert "kubernetes_pod_logs" in result.sources_queried

    async def test_k8s_tool_calls_recorded(self, sample_alert):
        # Given a configured K8s client
        obs_client = _make_datadog_client()
        k8s_client = _make_k8s_client()
        context = adapters.InvestigationContext(
            cluster_name="prod-us-east",
            namespace="default",
        )
        adapter = holmes_adapter.DirectToolsetAdapter(
            observability_client=obs_client,
            k8s_client=k8s_client,
        )

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert, context=context)

        # Then K8s tool calls are recorded alongside observability tool calls
        k8s_tools = [tc for tc in result.tool_calls if tc["tool"].startswith("k8s_")]
        assert len(k8s_tools) == 3
        tool_names = {tc["tool"] for tc in k8s_tools}
        assert tool_names == {"k8s_pod_status", "k8s_events", "k8s_pod_logs"}

    async def test_k8s_uses_service_as_resource_name(self, sample_alert):
        # Given a configured K8s client
        obs_client = _make_datadog_client()
        k8s_client = _make_k8s_client()
        context = adapters.InvestigationContext(
            cluster_name="prod-us-east",
            namespace="default",
        )
        adapter = holmes_adapter.DirectToolsetAdapter(
            observability_client=obs_client,
            k8s_client=k8s_client,
        )

        # When an investigation is run
        await adapter.investigate(alert=sample_alert, context=context)

        # Then K8s queries use the alert service name as the resource identifier
        k8s_client.get_pod_status.assert_called_once()
        call_kwargs = k8s_client.get_pod_status.call_args.kwargs
        assert call_kwargs["namespace"] == "default"

        k8s_client.get_recent_events.assert_called_once()
        events_kwargs = k8s_client.get_recent_events.call_args.kwargs
        assert events_kwargs["resource_name"] == "api-service"
        assert events_kwargs["namespace"] == "default"


class TestSummariseK8sPodStatus:
    def test_formats_running_pod(self):
        # Given pod status data for a running pod
        data = {
            "name": "api-abc123",
            "phase": "Running",
            "restart_count": 0,
            "conditions": [{"type": "Ready", "status": "True"}],
        }

        # When summarised
        result = holmes_adapter._summarise_k8s_pod_status(data)

        # Then the summary includes pod name, phase, and conditions
        assert "api-abc123" in result
        assert "Running" in result

    def test_formats_crashloop_pod(self):
        # Given pod status data for a crashing pod
        data = {
            "name": "api-abc123",
            "phase": "CrashLoopBackOff",
            "restart_count": 15,
            "conditions": [{"type": "Ready", "status": "False", "reason": "ContainersNotReady"}],
        }

        # When summarised
        result = holmes_adapter._summarise_k8s_pod_status(data)

        # Then the summary highlights the crash state and restart count
        assert "CrashLoopBackOff" in result
        assert "15" in result

    def test_returns_fallback_for_empty_data(self):
        # Given empty pod status data
        # When summarised
        result = holmes_adapter._summarise_k8s_pod_status({})

        # Then a fallback message is returned
        assert result  # Non-empty string


class TestSummariseK8sEvents:
    def test_formats_events(self):
        # Given K8s event data
        events = [
            {
                "type": "Warning",
                "reason": "OOMKilled",
                "message": "Container exceeded memory limit",
                "last_timestamp": "2024-01-01T00:00:00Z",
                "count": 3,
            },
        ]

        # When summarised
        result = holmes_adapter._summarise_k8s_events(events)

        # Then the summary includes event details
        assert "OOMKilled" in result
        assert "memory limit" in result

    def test_returns_no_events_message(self):
        # Given no events
        # When summarised
        result = holmes_adapter._summarise_k8s_events([])

        # Then a "no events" message is returned
        assert "No" in result
        assert "event" in result.lower()


class TestSummariseLogs:
    def test_empty_logs(self):
        # Given no log entries
        # When summarised
        result = holmes_adapter._summarise_logs([])

        # Then a "no logs" message is returned
        assert "No error logs found" in result

    def test_formats_entries(self):
        # Given log entries
        entries = [
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "message": "Connection refused",
                "service": "api",
            },
        ]

        # When summarised
        result = holmes_adapter._summarise_logs(entries)

        # Then entries are formatted with timestamp and service
        assert "Connection refused" in result
        assert "api" in result


class TestSummariseMetrics:
    def test_empty_metrics(self):
        # Given no metric series
        # When summarised
        result = holmes_adapter._summarise_metrics([])

        # Then a "no data" message is returned
        assert "No metric data found" in result

    def test_formats_series(self):
        # Given metric series
        series = [
            {"metric": "system.cpu.user", "scope": "host:web-01", "points": [[1, 2], [3, 4]]}
        ]

        # When summarised
        result = holmes_adapter._summarise_metrics(series)

        # Then the metric name and data point count are included
        assert "system.cpu.user" in result
        assert "2 data points" in result


class TestSummariseTraces:
    def test_empty_traces(self):
        # Given no trace spans
        # When summarised
        result = holmes_adapter._summarise_traces([])

        # Then a "no traces" message is returned
        assert "No error traces found" in result

    def test_formats_spans_with_duration(self):
        # Given trace spans
        spans = [{"service": "api", "resource": "/health", "duration_ns": 5_000_000}]

        # When summarised
        result = holmes_adapter._summarise_traces(spans)

        # Then spans include resource and duration in ms
        assert "/health" in result
        assert "5ms" in result
