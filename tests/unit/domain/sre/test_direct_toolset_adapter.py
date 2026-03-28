from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest import mock

import pytest

from sentinel.domain.resilience.circuit_breaker import CircuitBreaker
from sentinel.domain.sre import entities, holmes_adapter


@pytest.fixture
def sample_alert():
    return entities.Alert(
        id="P123ABC",
        source="pagerduty",
        title="High CPU usage on web-01",
        description="CPU usage exceeded 90% for 5 minutes",
        severity=entities.AlertSeverity.HIGH,
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
