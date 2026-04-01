"""
Unit tests for domain observability tool functions.

These functions are framework-agnostic (no PydanticAI dependency) so
tests validate raw input/output behaviour with fake clients.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from sentinel.domain.tools import observability as obs_tools
from sentinel.domain.vendor_adapters.observability import base as obs_base


class _FakeObsClient(obs_base.BaseObservabilityClient):
    """In-memory observability client for testing."""

    def __init__(
        self,
        *,
        configured: bool = True,
        logs: list[dict[str, Any]] | None = None,
        metrics: list[dict[str, Any]] | None = None,
        traces: list[dict[str, Any]] | None = None,
    ) -> None:
        self._configured = configured
        self._logs = logs or []
        self._metrics = metrics or []
        self._traces = traces or []

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def query_logs(
        self, *, query: str, time_range_minutes: int = 60, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self._logs

    async def query_metrics(
        self, *, query: str, time_range_minutes: int = 60
    ) -> list[dict[str, Any]]:
        return self._metrics

    async def query_traces(
        self, *, query: str, time_range_minutes: int = 60, limit: int = 50
    ) -> list[dict[str, Any]]:
        return self._traces


class TestQueryRecentLogs:
    async def test_returns_unavailable_when_client_is_none(self) -> None:
        # Given no client
        # When querying logs
        result = await obs_tools.query_recent_logs(client=None, service="api", query="error")
        # Then unavailability message returned
        assert "not available" in result.lower()

    async def test_returns_unavailable_when_client_unconfigured(self) -> None:
        # Given an unconfigured client
        client = _FakeObsClient(configured=False)
        # When querying logs
        result = await obs_tools.query_recent_logs(client=client, service="api", query="error")
        # Then unavailability message returned
        assert "not available" in result.lower()

    async def test_returns_formatted_log_entries(self) -> None:
        # Given a client with log results
        client = _FakeObsClient(
            logs=[
                {
                    "timestamp": "2026-04-01T10:00:00Z",
                    "message": "Connection timeout to DB",
                    "status": "error",
                },
                {
                    "timestamp": "2026-04-01T10:01:00Z",
                    "message": "Retry succeeded",
                    "status": "warn",
                },
            ]
        )
        # When querying logs
        result = await obs_tools.query_recent_logs(client=client, service="api", query="timeout")
        # Then formatted entries returned
        assert "2 log entries" in result
        assert "Connection timeout" in result
        assert "Retry succeeded" in result

    async def test_returns_no_match_when_empty(self) -> None:
        # Given a client with no results
        client = _FakeObsClient(logs=[])
        # When querying logs
        result = await obs_tools.query_recent_logs(client=client, service="api", query="error")
        # Then no-match message returned
        assert "no matching logs" in result.lower()

    async def test_handles_query_failure_gracefully(self) -> None:
        # Given a client that raises on query
        client = _FakeObsClient()
        client.query_logs = AsyncMock(side_effect=ConnectionError("Datadog API down"))
        # When querying logs
        result = await obs_tools.query_recent_logs(client=client, service="api", query="error")
        # Then failure message returned
        assert "failed" in result.lower()
        assert "ConnectionError" in result

    async def test_truncates_beyond_ten_entries(self) -> None:
        # Given a client with 15 log entries
        entries = [
            {"timestamp": f"2026-04-01T10:{i:02d}:00Z", "message": f"Entry {i}", "status": "error"}
            for i in range(15)
        ]
        client = _FakeObsClient(logs=entries)
        # When querying logs
        result = await obs_tools.query_recent_logs(client=client, service="api", query="error")
        # Then only first 10 shown with truncation note
        assert "15 log entries" in result
        assert "5 more entries" in result


class TestQueryMetrics:
    async def test_returns_unavailable_when_client_is_none(self) -> None:
        # Given no client
        result = await obs_tools.query_metrics(client=None, service="api", metric_name="cpu")
        # Then unavailability message returned
        assert "not available" in result.lower()

    async def test_returns_formatted_metric_series(self) -> None:
        # Given a client with metric results
        client = _FakeObsClient(
            metrics=[
                {
                    "metric": "cpu.usage",
                    "scope": "host:web-1",
                    "points": [[1000, 45.2], [1060, 78.9], [1120, 52.1]],
                },
            ]
        )
        # When querying metrics
        result = await obs_tools.query_metrics(client=client, service="api", metric_name="cpu")
        # Then formatted series returned with stats
        assert "1 metric series" in result
        assert "min=45.20" in result
        assert "max=78.90" in result
        assert "latest=52.10" in result

    async def test_returns_no_match_when_empty(self) -> None:
        # Given a client with no results
        client = _FakeObsClient(metrics=[])
        # When querying metrics
        result = await obs_tools.query_metrics(client=client, service="api", metric_name="cpu")
        # Then no-match message returned
        assert "no metric data" in result.lower()


class TestQueryErrorTraces:
    async def test_returns_unavailable_when_client_is_none(self) -> None:
        # Given no client
        result = await obs_tools.query_error_traces(client=None, service="api")
        # Then unavailability message returned
        assert "not available" in result.lower()

    async def test_returns_formatted_traces(self) -> None:
        # Given a client with trace results
        client = _FakeObsClient(
            traces=[
                {
                    "trace_id": "abc123def456",
                    "resource": "POST /api/users",
                    "duration_ns": 1_500_000_000,
                    "status": "error",
                },
            ]
        )
        # When querying traces
        result = await obs_tools.query_error_traces(client=client, service="api")
        # Then formatted traces returned
        assert "1 error traces" in result
        assert "abc123def456"[:12] in result
        assert "POST /api/users" in result
        assert "1500ms" in result

    async def test_returns_no_match_when_empty(self) -> None:
        # Given a client with no results
        client = _FakeObsClient(traces=[])
        # When querying traces
        result = await obs_tools.query_error_traces(client=client, service="api")
        # Then no-match message returned
        assert "no error traces" in result.lower()
