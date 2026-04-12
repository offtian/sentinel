"""
Tests for structured logging in MCP tool handlers.
"""

from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from sentinel.interfaces.mcp.tools import documentation as doc_tools
from sentinel.interfaces.mcp.tools import observability as obs_tools
from sentinel.utils import logs


class TestObservabilityToolLogging:
    @pytest.mark.asyncio
    async def test_query_logs_logs_invocation_and_completion(self) -> None:
        # Given a mock observability client
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.query_logs.return_value = []

        # When calling the MCP tool function
        with mock.patch.object(logs, "log_event") as mock_log:
            await obs_tools.query_logs(
                obs_client=mock_client,
                service="api-service",
                query="error",
                minutes_back=30,
            )

        # Then invocation and completion events are logged
        event_names = [call.args[0] for call in mock_log.call_args_list]
        assert "mcp_tool_invoked" in event_names
        assert "mcp_tool_completed" in event_names

    @pytest.mark.asyncio
    async def test_query_logs_logs_tool_name_in_params(self) -> None:
        # Given a mock observability client
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.query_logs.return_value = []

        # When calling the MCP tool function
        with mock.patch.object(logs, "log_event") as mock_log:
            await obs_tools.query_logs(
                obs_client=mock_client,
                service="api-service",
                query="error",
                minutes_back=30,
            )

        # Then the tool name is included in the invocation log params
        invoked_call = next(c for c in mock_log.call_args_list if c.args[0] == "mcp_tool_invoked")
        assert invoked_call.kwargs["params"]["tool"] == "query_logs"

    @pytest.mark.asyncio
    async def test_query_logs_completion_includes_duration_ms(self) -> None:
        # Given a mock observability client
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.query_logs.return_value = []

        # When calling the MCP tool function
        with mock.patch.object(logs, "log_event") as mock_log:
            await obs_tools.query_logs(
                obs_client=mock_client,
                service="api-service",
                query="error",
                minutes_back=30,
            )

        # Then the completion log includes duration_ms
        completed_call = next(
            c for c in mock_log.call_args_list if c.args[0] == "mcp_tool_completed"
        )
        assert "duration_ms" in completed_call.kwargs["params"]
        assert isinstance(completed_call.kwargs["params"]["duration_ms"], float)

    @pytest.mark.asyncio
    async def test_query_metrics_logs_invocation_and_completion(self) -> None:
        # Given a mock observability client
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.query_metrics.return_value = []

        # When calling the MCP tool function
        with mock.patch.object(logs, "log_event") as mock_log:
            await obs_tools.query_metrics(
                obs_client=mock_client,
                service="api-service",
                metric_name="cpu",
                minutes_back=60,
            )

        # Then invocation and completion events are logged
        event_names = [call.args[0] for call in mock_log.call_args_list]
        assert "mcp_tool_invoked" in event_names
        assert "mcp_tool_completed" in event_names

    @pytest.mark.asyncio
    async def test_query_error_traces_logs_invocation_and_completion(self) -> None:
        # Given a mock observability client
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.query_error_traces.return_value = []

        # When calling the MCP tool function
        with mock.patch.object(logs, "log_event") as mock_log:
            await obs_tools.query_error_traces(
                obs_client=mock_client,
                service="api-service",
                minutes_back=30,
            )

        # Then invocation and completion events are logged
        event_names = [call.args[0] for call in mock_log.call_args_list]
        assert "mcp_tool_invoked" in event_names
        assert "mcp_tool_completed" in event_names


class TestDocumentationToolLogging:
    @pytest.mark.asyncio
    async def test_search_documentation_logs_invocation_and_completion(self) -> None:
        # Given a mock document searcher
        mock_searcher = mock.AsyncMock()
        mock_searcher.search.return_value = []

        # When calling the MCP tool function
        with mock.patch.object(logs, "log_event") as mock_log:
            await doc_tools.search_documentation(
                document_searcher=mock_searcher,
                query="deployment guide",
                max_results=5,
            )

        # Then invocation and completion events are logged
        event_names = [call.args[0] for call in mock_log.call_args_list]
        assert "mcp_tool_invoked" in event_names
        assert "mcp_tool_completed" in event_names

    @pytest.mark.asyncio
    async def test_search_documentation_logs_tool_name(self) -> None:
        # Given a mock document searcher
        mock_searcher = mock.AsyncMock()
        mock_searcher.search.return_value = []

        # When calling the MCP tool function
        with mock.patch.object(logs, "log_event") as mock_log:
            await doc_tools.search_documentation(
                document_searcher=mock_searcher,
                query="deployment guide",
                max_results=5,
            )

        # Then the tool name is included in the invocation log params
        invoked_call = next(c for c in mock_log.call_args_list if c.args[0] == "mcp_tool_invoked")
        assert invoked_call.kwargs["params"]["tool"] == "search_documentation"


class TestObservabilityToolErrorLogging:
    @pytest.mark.asyncio
    async def test_query_logs_logs_exception_on_error(self) -> None:
        # Given the domain tool raises an unhandled error
        with (
            mock.patch(
                "sentinel.interfaces.mcp.tools.observability.obs_tools.query_recent_logs",
                side_effect=RuntimeError("connection failed"),
            ),
            mock.patch.object(logs, "log_exception") as mock_log_exc,
            mock.patch.object(logs, "log_event"),
            contextlib.suppress(RuntimeError),
        ):
            # When calling the MCP tool function
            await obs_tools.query_logs(
                obs_client=mock.AsyncMock(),
                service="api-service",
                query="error",
                minutes_back=30,
            )

        # Then the exception is logged with tool name
        assert mock_log_exc.called
        assert mock_log_exc.call_args.kwargs["params"]["tool"] == "query_logs"
