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
    async def test_trigger_investigation_inserts_job_and_returns_id(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When triggering an investigation
        result = await inv_tools.trigger_investigation(
            db=mock_db,
            alert_source="pagerduty",
            alert_id="P123",
            description="CPU spike on api-service",
        )

        # Then a job is enqueued and the result contains a job ID
        mock_db.execute.assert_called_once()
        assert "P123" in result
        assert "job_id=" in result

    @pytest.mark.asyncio
    async def test_trigger_investigation_returns_error_when_db_none(self) -> None:
        # Given no database connection
        # When triggering an investigation
        result = await inv_tools.trigger_investigation(
            db=None,
            alert_source="pagerduty",
            alert_id="P123",
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_get_investigation_status_returns_job_status(self) -> None:
        # Given a mock database with a completed job
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = {
            "id": "abc-123",
            "status": "completed",
            "job_type": "SRE_INVESTIGATION",
            "created_at": "2026-04-03T12:00:00+00:00",
        }

        # When checking status
        result = await inv_tools.get_investigation_status(
            db=mock_db,
            investigation_id="abc-123",
        )

        # Then the status is returned
        assert "completed" in result.lower()

    @pytest.mark.asyncio
    async def test_get_investigation_status_returns_not_found(self) -> None:
        # Given a mock database with no matching job
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When checking status
        result = await inv_tools.get_investigation_status(
            db=mock_db,
            investigation_id="nonexistent",
        )

        # Then a not-found message is returned
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_get_investigation_status_returns_fallback_when_db_none(self) -> None:
        # Given no database connection
        # When checking status
        result = await inv_tools.get_investigation_status(
            db=None,
            investigation_id="abc-123",
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()
