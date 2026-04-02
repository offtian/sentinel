from __future__ import annotations

from unittest import mock

import pytest

from sentinel.interfaces.mcp.tools import investigation as inv_tools
from sentinel.interfaces.mcp.tools import observability as mcp_obs_tools


class TestMcpObservabilityTools:
    @pytest.mark.asyncio
    async def test_query_logs_delegates_to_domain_tool(self) -> None:
        # Given a mock observability client
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.query_logs.return_value = [
            {"timestamp": "2026-04-02T12:00:00Z", "message": "Error connecting", "status": "error"}
        ]

        # When calling the MCP tool function
        result = await mcp_obs_tools.query_logs(
            obs_client=mock_client,
            service="api-service",
            query="error",
            minutes_back=30,
        )

        # Then it returns a formatted result
        assert "api-service" in result or "Error" in result

    @pytest.mark.asyncio
    async def test_query_logs_returns_fallback_when_client_none(self) -> None:
        # Given no observability client
        # When calling the MCP tool function
        result = await mcp_obs_tools.query_logs(
            obs_client=None,
            service="api-service",
            query="error",
            minutes_back=30,
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()


class TestMcpInvestigationTools:
    @pytest.mark.asyncio
    async def test_trigger_investigation_returns_confirmation(self) -> None:
        # Given valid alert info
        # When triggering an investigation
        result = await inv_tools.trigger_investigation(
            alert_source="pagerduty",
            alert_id="P123",
            description="test",
        )

        # Then a confirmation message is returned
        assert "pagerduty" in result
        assert "P123" in result

    @pytest.mark.asyncio
    async def test_get_investigation_status_returns_placeholder(self) -> None:
        # Given an investigation ID
        # When checking status
        result = await inv_tools.get_investigation_status(investigation_id="inv-001")

        # Then a status message is returned
        assert "inv-001" in result
